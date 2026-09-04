# `dskit.production` — the production layer (proposal for ADR-0082 / ADR-0083)

**Status:** proposed 2026-09-04 (Fable synthesis of the nine research reports in
`research_reports/`; Opus skeptic review pending; owner approval required before
any file under `dskit/production/` exists).

**What the owner is asked to approve.** (1) The file-by-file structure in §8.
(2) The design decisions D1–D24 in §3. (3) The pipeline change in §9.1 (a public
subgraph-rerun seam, ADR-0083). (4) The build phases and model assignment in §11.
(5) The open choices in §12. Everything else is the reasoning behind those.

How to read: §1–§3 are the ruling; §4–§7 are the contracts a test can be written
from; §8–§11 are what lands where and in which order; §13 traces every piece to
the report that grounds it.

---

## 1. The one-paragraph model

A **serve document** (JSON, its own identity hash) declares a production process:
which training **run** it serves (the run dir's `config.json` and artifacts are
read, never restated), where live rows **enter** that run's own node graph and how
wide the window is, which node keys are the decision **heads**, how head outputs
become domain-neutral **proposals**, how ticks are **scheduled** (clock, calendar,
cadence), which **guards** every proposal must pass, which **executor** acts,
which **monitors** watch the decision stream, and where the **ledger** lives. One
`ServeLoop` class runs it: each tick is a fixed phase order — gate, fetch,
decide, watermark, guard, record intent, act, record outcome, checkpoint — with
exactly one `tick` and one `decision` record per tick in a hash-chained,
append-only ledger, and one `dskit.journal` production row per process. Backtest,
shadow, paper, and live are the *same* document run with different injected
objects (clock, feed, executor); moving real money requires a recorded, expiring,
two-principal **arming** bound to the document's identity hash — never a config
key. The decision itself is made by re-executing the training run's own nodes in
`mode: load`, so the number that reaches the venue is the number the backtest
scored.

## 2. Scope

**Generic — belongs in `dskit/production/` (tier 1, stdlib + dskit siblings):**
the serve-document grammar and identity; clock / calendar / cadence; the feed
seam over onboarding; the decider (serving-document derivation + subgraph
re-execution); proposals, guards, limits, the breaker, arming; the executor ABC
with shadow / paper / recorded executors and a conformance battery; the ledger,
checkpoint, and reconciliation; monitors; alerts, health, heartbeats; resilience
policies (retry, circuit breaker, rate limiter, transport); outcomes, attribution,
replay parity; the readiness checklist; the CLI.

**Child (tier 3) — never in dskit:** the venue executor subclass (translation of
units, order types, error codes, dedup and replace semantics), the proposer when a
head's output shape is bespoke, the measures a child's exposure needs, every
threshold and limit *value*, the calendar's contents, the readiness checklist's
content, credentials (env-var names in config, values in the environment).

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
- **D2 — Mode is the objects passed in, never a branch.** Shadow / paper / live
  differ only in which `Executor` (and clock/feed for replay) the loop is handed
  [R7, R9]. There is no `if mode ==` in `loop.py`; the conformance test asserts
  it (AST scan for `mode ==` / `rung ==` in the loop module).
- **D3 — The decision is a re-execution of the run's own subgraph.** The decider
  derives a *serving document* from `<run-dir>/config.json` (trainables flipped
  to `mode: "load"` pinned to `artifacts/<key>`; search winners applied and search
  nodes dropped; gate/stat_test verdicts replayed from the run's records) and
  re-executes `ancestors(heads) ∪ heads` through a public pipeline seam with
  exactly one override — the entry node's window bound [owner constraints 1, 3;
  R2 Rule #32; R6 §2.6]. Rejected: constructing nodes by hand in the loop
  (`intraday_poc/live.py` does this for two nodes and is 1,368 lines).
- **D4 — Live rows enter through the connector seam.** The feed is `acquire
  --mode live` (`run_acquisition`) or a read of a store another `watch` process
  fills; the run's data node reads the same onboarding root it was trained from
  [owner constraint 2; ADR-0046]. Rejected: a second vendor fetch beside the
  connector (the audited `adjustment` drift).
- **D5 — The pipeline's `clock`/`schedule` sections stay as they are.** Cadence
  belongs to the serve document: a training document describes a computation, a
  serve document describes a process, and research cadence (event-grid,
  ADR-0047) is a data fact, not a scheduler [R7 §2.10 argued the opposite; this
  proposal keeps the engine runtime-free]. Open choice §12.1.
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
  refuse < hold < halt`; the chain runs every guard and records every finding
  with value, bound, reason; composite = max; cancels are never guarded or
  throttled [R5 §2.1, R1 §1.2]. `Limit` is ONE class parameterised by measure ×
  window × bound × scope over a stdlib measure registry plus a `pkg.module:Class`
  doorway [R5 §2.3]. Default-deny extends to limits: a document whose executor can
  reach live must declare a per-proposal size limit and a period loss limit or
  `plan` refuses [R5, FIA].
- **D10 — Three orthogonal permission states: rung, breaker, health.** Rung
  (`shadow < paper < live_limited < live`) comes from the arming record; breaker
  (`active → reducing → halted`) from guards, feed, executor link, reconciliation
  or the operator; health (`starting → ready | degraded | unhealthy → stopping`)
  from probes and staleness. A proposal reaches the venue only when all three
  permit; effective rung = `min(rung, breaker)` [R5 §2.4, R4 §2.1, R1 §1.4].
- **D11 — Arming is a recorded human act, not a config key.** `arm` writes an
  arming record (rung, principals — two distinct for ≥ `live_limited`,
  `armed_until` mandatory, allowlist, limits overlay, the serve document's
  identity hash); serving at a live rung additionally requires `--armed` on the
  CLI and `DSKIT_PRODUCTION_ARM=<doc hash>` in the environment; the live executor
  is constructed only with a valid arming and raises `NotArmed` before any I/O;
  with no arming the same document serves at `shadow`, loudly (banner, first
  tick record, journal row all name the rung). Demotion (`disarm`) is free
  [R5 §2.4, R1 §2.1, R3 §2.5]. `paper=True` in existing children stays.
- **D12 — Halt never flattens.** Halt = refuse new proposals + best-effort cancel
  of working orders (outcome recorded: `none | submitted | failed | partial |
  unknown`) + optional reduce-only; flattening is a human act (enter `reducing`;
  the model's reduce-only proposals do it under the same guards). Breaker reset
  is human-only after `cooling_off`, persisted across restarts, never a timer
  [R5 §1.2, R1 postmortems].
- **D13 — Record before act, checkpoint last, reconcile before deciding.** The
  intent record (with its idempotency key) is durable before `executor.submit`;
  an executor call that raises after the request leaves `unknown`, which only
  `executor.order(ref)` may resolve — never a blind resend; the checkpoint is
  written last; startup reconciles the ledger against the venue before `READY`
  and applies `on_mismatch ∈ {halt, adopt, refuse}` [R7 §2.4–2.6, R3 §1.2,
  R1 §1.1].
- **D14 — The executor is the smallest venue-neutral verb set** (`spec`,
  `capabilities`, `check`, `submit`, `cancel`, `order`, `open_orders`, `fills`,
  `balances` abstract; `positions`, `settlements`, `replace`, `events`,
  `venue_time_ms`, `cancel_all` concrete). Eleven order statuses, terminality
  preserved, a venue lacking a state collapses toward less certainty; fills carry
  `status ∈ {pending, final, reversed}`; native units carried with a declared
  label, money as `Decimal` at the boundary [R8 §2.1–2.3]. Core ships
  `ShadowExecutor`, `PaperExecutor` (declared fill/slippage/fee/latency
  assumptions, seeded), `RecordedExecutor` (replay).
- **D15 — One append-only, hash-chained ledger per process; state is a fold.**
  Closed record kinds; envelope `kind, id, seq, process_id, recorded_at_ms,
  schema_version, prev_hash, hash`; JSONL first (single write on `O_APPEND`,
  fsync policy, torn-tail recovery, `flock`, segment rotation carrying the
  chain), sqlite as a pack; corrections supersede, never mutate; the head hash is
  anchored into the journal row at stop [R6 §2.1–2.4, R4 §1.3].
- **D16 — Monitors are strategy objects with a first-class `insufficient`.**
  `Monitor.fit/observe/verdict/state/restore`; families operational, stream,
  distribution, outcome; `Reference`, `Chunker`, `Threshold`, `Response`
  strategies; verdicts `ok | warn | alarm | insufficient`; below `min_n` never
  `ok`; keep a fixed anchor and a rolling reference [R2 §2].
- **D17 — Alert on symptoms; closed severities; sinks that cannot kill the
  loop.** Severities `info | warning | error | critical` pinned to PagerDuty /
  OTel / syslog / logging levels; `AlertSink` validates loudly at construction,
  bounds its own timeout, and every failure is swallowed and counted; a bounded
  worker queue; dedup, `group_wait_s`, `repeat_interval_s`, rate limit [R4 §2].
- **D18 — Health is a state machine, the dead-man's switch is external.** Local
  failure → `unhealthy` (stop acting *and* stop heartbeating); dependency failure
  or staleness → `degraded` (observe, refuse acts); heartbeat emitters `file`
  and `url` (healthchecks-style, `rid=tick_id`); the package never pages on its
  own death [R4 §1.2, §2.1].
- **D19 — Resilience policies are pure stdlib objects with injected clock,
  sleeper, and rng.** Outcome kinds `ok | transient | throttled | fatal |
  ambiguous`; an ambiguous WRITE may never retry, only reconcile; full-jitter
  backoff capped at `MAX_BACKOFF_S` (imported from onboarding — one ceiling);
  a breaker per scope with `min_calls` small; token buckets per scope with a
  write bulkhead of one [R3 §2].
- **D20 — Replay parity is a test the package runs, not a promise.** The ledger
  plus recorded executor responses are the tape; `replay` re-executes the same
  nodes on the recorded rows with `TestClock` + `RecordedExecutor` and diffs legs
  under a closed divergence vocabulary [R6 §2.6, R7 §2.9, R9].
- **D21 — Outcomes are bitemporal, joined by id, reported as-of.** `outcome`
  records carry `effective_at_ms`, `known_at_ms`, `terminal`, `supersedes`;
  reports are computed at a `known_at ≤ T` cut; derived labels use a strict
  forward as-of join; settled and marked never share a series [R6 §2.5].
- **D22 — The journal stays one row per process.** The loop calls
  `record_production` once at stop (head anchor and exception in `notes`);
  `arm`, `disarm`, `halt`, `resume` are also production rows because they are
  human acts on the path to production. No journal code changes.
- **D23 — Single-threaded loop; one worker thread for alert delivery.** Tick
  phases run sequentially for deterministic ordering [R9 Nautilus]; probes run
  inline with timeouts (phase 1); the only thread is the alert router's bounded
  queue consumer.
- **D24 — Identity follows the pipeline recipe.** `canonical_hash` (notes stripped
  everywhere) over the serve document with `alerts`, `heartbeat`, `ledger`, and
  `env` excluded (placement); optional fields emitted only when present from
  day one; the serve root is keyed by `<name>-<hash8>`.

## 4. The serve document

### 4.1 Grammar (default-deny at every level; `notes` allowed everywhere)

```jsonc
{
  "name": "yourproject-serve",
  "notes": "Why this process exists and how to promote it — the 'why'.",
  "serving": {
    "run_dir": "pipeline_runs/train-2026-01-01-abcd1234",   // the run served; config.json + artifacts read from here
    "adapter": "yourproject",                                 // import = registration (same as --adapter)
    "entry": {"node": "bars", "param": "since_ms", "window_ms": 14400000},
    "heads": ["select"],                                      // node keys whose outputs form the proposals
    "proposer": {"uses": "intent-rows", "params": {"output": "picks", "fields": {"instrument": "symbol", "side": "side", "qty": "qty"}}},
    "replay": {"gate": "recorded", "survivors": "recorded"},  // gate/stat_test nodes replayed from the run's records
    "max_artifact_age": "P30D"                                // refuse a run older than this (FreqAI expiration_hours)
  },
  "feed": {"uses": "acquire", "params": {"root": "./ob", "source": "alpaca", "stream": "bars"}},
  "schedule": {
    "clock": {"uses": "wall"},
    "calendar": {"uses": "weekly-sessions", "params": {"tz": "America/New_York",
                 "sessions": [{"days": ["mon", "tue", "wed", "thu", "fri"], "open": "09:30", "close": "16:00"}],
                 "holidays": ["2026-11-26"], "after_open_s": 60, "before_close_s": 120}},
    "cadence": {"uses": "aligned-bar", "params": {"bar_ms": 60000, "publish_delay_ms": 5000}},
    "overrun": {"policy": "coalesce", "max_lag_ms": 30000},
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

`hash = canonical_hash(document with notes stripped, minus alerts / heartbeat /
ledger / env)`. Graded: `serving`, `feed`, `schedule`, `guards`, `execution`,
`reconcile`, `monitors`, `health`. The serve root is
`<ledger.root>/<name>-<hash8>/`. Changing what the process decides, gates, or
acts with starts a new serve series; moving alerts or the ledger does not.

### 4.3 `uses` resolution

Every `uses` is a registered kind name or a `pkg.module:Class` reference, resolved
exactly as pipeline nodes and onboarding connectors are (import = registration);
each family has its own registry (`CALENDAR_KINDS`, `CADENCE_KINDS`,
`FEED_KINDS`, `PROPOSER_KINDS`, `GUARD_KINDS`, `MEASURE_KINDS`,
`EXECUTOR_KINDS`, `MONITOR_KINDS`, `PROBE_KINDS`, `SINK_KINDS`,
`HEARTBEAT_KINDS`); registering a name twice refuses. A child registers nothing
and references its classes by path.

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
dead, closed}, acq_id, records_added, pulled_at_ms}`; `status` is the feed's own
health (a pull that adds nothing for `dead_after` consecutive ticks is `dead`).
`AcquireFeed` (`root`, `source`, `stream`; runs `run_acquisition(..., mode="live")`
through the onboarding root and registry the way the onboarding CLI builds them);
`StoreFeed` (no pull; another process runs `onboarding watch`; reports staleness
from the store's newest instant via `scan_stream`); `ReplayFeed` (iterates recorded
`tick` records, drives the `ReplayClock`, exposes the recorded `inputs_digest`).

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
- `Decider`: `prepare(registry, asof, base_run_dir)` builds a `SubgraphRunner`
  (§9.1) over the serving document — base pass once per process, load-mode
  artifact verification by the packs; `decide(tick_at_ms) -> Decision` reruns the
  needed∩dirty subgraph under the single override
  `{"<entry>.<param>": tick_at_ms - window_ms}`, reads the entry node's newest
  instant as the watermark (`data_asof_ms`), digests the entry records
  (`inputs_digest` via `stream_digest`), and hands head outputs to the proposer.
- `Proposer(ABC)`: `proposals(head_outputs, state) -> list[Proposal]`,
  `quotes(head_outputs) -> list[Quote]` (default: `MarketRecord`-shaped rows via
  the records module's accessors). `IntentRows` (`output`, `fields` map,
  `default_tif`), `TargetPositions` (`output`, `fields`, diff against
  `state.positions`).
- `RecordedOutputs` note: the replacement node must satisfy the planner's role
  rules for the role it replaces (a `capital` node requires a `stat_test`
  survivors wire); the phase-1 build validates the exact mechanism test-first
  against the planner, one subclass per replayed role. **Open for review:**
  whether replaying verdicts is the right rule or a needed gate/stat_test node
  should simply refuse (§12.3).
- Refusals at `plan`: entry node absent or `param` not in its `_PARAMS`; a head
  absent; a trainable in the subgraph without an artifact dir; an artifact older
  than `max_artifact_age`; a needed search node without a recorded winner; a
  `$prev` reference; a serve document whose adapter does not import.

### 5.4 `records.py` — value objects

All money/quantity/price fields are `Decimal` (strings in JSON); instants are
epoch-ms ints; each record has `to_obj`/`from_obj` (default-deny) and refuses a
non-finite number.

- `Quote{instrument, bid, ask, mid, asof_ms}`.
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

### 5.5 `guards.py`

- `Guard(ABC)`: class attribute `_PARAMS`; `validate_params` classmethod;
  `@abstractmethod check(proposal, state) -> Finding`. `state` is read-only:
  positions, working orders, realised/unrealised P&L series, decision history,
  feed status and ages, calendar, breaker, rung.
- `GuardChain(guards)`: runs every guard, records every finding, composite =
  max over the lattice `allow < warn < amend < refuse < hold < halt`; an
  `amend` clips the single scalable field and records the original beside the
  amended proposal; `hold` queues with `ttl` and expires as `refuse` (phase 2:
  an `approve` verb); `halt` trips the breaker.
- `Limit(Guard)`: `measure` (registered name or class ref), `window` ∈ `{}` |
  `{duration}` | `{count}` | `{calendar: session|day|event}`, `bound` (`max`
  and/or `min`, decimal strings or ints, inclusive), `warn_at ∈ (0,1)`, `scope`
  ∈ `aggregate | per_key | {group: field}`, `include_working` (default true),
  `on_breach ∈ {refuse, amend, pause, hold, halt}` (`amend` only for scalable
  measures; `pause` needs `pause: {duration | calendar}`; `hold` needs `hold:
  {ttl}`).
- `Measure(ABC)`: `value(proposal, state, window, scope_key) -> Decimal | float`.
  Registry (stdlib): `quantity, notional, exposure, exposure_after,
  price_deviation, pnl, drawdown, consecutive_losses, decision_count,
  identical_count, direction_changes, open_orders, input_age_ms, feed_age_ms,
  confidence, bankroll_fraction, error_vs_realised`. A child's exposure formula is
  a `Measure` subclass referenced by path.
- `RangeGuard(Guard)`: `field`, `min`, `max`, `nan ∈ {refuse, allow}`.
- Cancels never pass through the chain (a structural rule pinned by a test).

### 5.6 `breaker.py` and `arming.py`

- `Breaker`: states `active | reducing | halted`; `trip(reason, actor)` from a
  guard `halt`, `feed.dead`, `executor.link_lost`, `reconcile.mismatch`,
  `operator`; `reduce(actor)`; `reset(actor, acknowledges_trip_id)` refused
  before `cooling_off_s` elapses or without a trip id; state persisted in
  `breaker.json` and reloaded before `READY`; every transition is a `trip`
  record; the kill-switch file `HALT` in the serve root is polled every tick;
  on entering `halted` the loop cancels working orders per `execution.on_halt`
  and records the outcome vocabulary.
- `Arming{rung ∈ RUNGS, doc_hash, armed_by[], armed_at_ms, armed_until_ms,
  allowlist[], limits_overlay{}, notes}`: `arm` refuses a rung above the
  executor's reach, fewer than two distinct principals for ≥ `live_limited`, a
  missing expiry, a hash mismatch; `disarm` always succeeds; `current(root)`
  returns `shadow` when absent or expired; `check_conjunction(doc, cli_armed,
  env)` is what `serve` calls. Every arming change is an `arming` ledger record
  and a journal production row.

### 5.7 `executor.py`

- `Executor(ABC)`: `spec()` (default-deny knobs; secret knobs name env vars),
  `capabilities()` (`tifs`, `market_orders`, `notional`, `positions ∈ {venue,
  derived}`, `settlements`, `stream`, `replace ∈ {native, cancel_submit, none}`,
  `dedupe ∈ {replays, rejects, window, none}`, `units {qty, price, cash}`,
  `position_model ∈ {netting, hedging}`); `@abstractmethod check(config)`,
  `submit(intent) -> Ack`, `cancel(ref) -> Ack`, `order(ref) -> OrderState`,
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
- `NotArmed` raised by a live executor constructed without a valid arming
  (the constructor receives the `Arming`; `LiveExecutor(ABC)` subclass hook
  `_open(config, arming)`).
- `executor_conformance_suite(cls, params, quotes)`: a pytest class builder (the
  node `conformance_suite` precedent) running the 15-item battery [R8 §2.5]:
  default-deny spec; `check` performs no submit; `client_ref` echoed; same
  `client_ref` twice ⇒ same `venue_ref` or `DuplicateRef`; terminal absorption;
  `filled_qty` monotone except reversed; capability gating before I/O; no
  duplicate `fill_id`; units pinned; derived vs venue positions agree; unarmed ⇒
  `NotArmed`; shadow has no network; paper deterministic; vocabulary restated in
  the test.

### 5.8 `ledger.py`

- `Ledger(ABC)`: `append(record) -> seq`, `append_many`, `scan(kind=None,
  since_seq=0)`, `head() -> (seq, hash)`, `verify() -> first_bad_seq | None`,
  `snapshot(state)`, `latest_snapshot()`. Record hash = `sha256(prev_hash +
  canonical(record − hash))`, genesis 64 zeros, computed after `seq`/`prev_hash`;
  canonical form = the pipeline recipe WITHOUT notes stripping and with no floats
  in money fields; idempotent append (same `id` + same hash → existing `seq`;
  same `id` + different content → refuse); dense `seq`; readers tolerate unknown
  fields and upcast `schema_version`.
- `JsonlLedger`: one serialised line per single `write()` on `O_APPEND`; `fsync ∈
  {every, batch:{n, ms}, none}` (`none` legal only at `shadow`); `flock` for the
  process lifetime; torn last line truncated on open and a `process{event:
  recovered}` record appended; rotation by `size | day | process` into
  `ledger.NNNN.jsonl` carrying `seq`/`prev_hash`; directory fsync on segment
  creation; never copytruncate.
- `Checkpoint` (atomic JSON, not a ledger record): `loop_id, doc_hash,
  serving_hash, run_hash, last_tick_at, last_completed_tick_at, pending[]
  (client refs without a terminal outcome), positions_snapshot_at, halt, schema_version`.
- Serve root layout:

```
<ledger.root>/<name>-<hash8>/
├── document.json      the serve document verbatim
├── arming.json        current arming (absent = shadow); arming history is in the ledger
├── breaker.json       breaker state, reloaded before READY
├── checkpoint.json    atomic loop checkpoint
├── HALT               kill-switch file (operator or `halt` verb); absent = not halted by file
├── serve.lock         flock single instance
├── heartbeat.json     file heartbeat (mtime + status)
├── ledger/            ledger.0001.jsonl … (the hash chain)
└── process-<id>/base/ the base-pass run dir of the serving document (config/plan/resolved/nodes)
```

### 5.9 `reconcile.py`

`Reconciler.run(ledger_state, executor, scope) -> ReconReport{breaks[],
status}`: resolve every `pending` client ref via `executor.order`; compare open
orders, fill-derived positions vs venue positions (when `capabilities.positions ==
venue`), balances; break classes `timing | missing_in_ledger | missing_at_venue |
quantity | price | fee | state | settlement`; severity `info | warn | block`;
policy `on_mismatch ∈ {halt (exit 3), adopt (record the delta, venue is truth),
refuse (exit 1)}`; unknown venue orders recorded as `external`, never adopted as
ours; runs at start (before `READY`) and every `every_s`; always appends a `recon`
record, even when clean; never synthesises a venue action.

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

- `AlertSink(ABC)`: `KIND`, `_PARAMS` (`timeout_s` required and bounded),
  constructor validates loudly and probes reachability (the mlflow-pack rule);
  `@abstractmethod send(alert)`; `close()`. Kinds: `log`, `memory`, `email`
  (smtplib, `timeout`), `webhook` (urllib JSON POST; `template ∈ {slack,
  discord, pagerduty, generic}`; the URL comes from an env var NAME).
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
- `Heartbeat` emitters: `file` (mtime + JSON status), `url` (GET/POST with
  `rid=tick_id`, `/start`, `/fail`, `timeout_s`); `every_s ≤ period`; sent only
  in `ready` (or `degraded` when configured). Phase 2: `systemd` (`NOTIFY_SOCKET`
  datagrams).
- Single instance: `flock(LOCK_EX | LOCK_NB)` on `serve.lock`; a second copy
  exits 4. Signals: SIGTERM/SIGINT set a stop flag; `waiting` wakes within 1 s;
  `ticking` finishes the phase and never stops between act and record-outcome;
  `shutdown_grace_s` [1, 300] must be under the supervisor's grace.

### 5.12 `resilience.py`

`Classifier(ABC)` (`classify(outcome) -> kind ∈ {ok, transient, throttled, fatal,
ambiguous}`; default HTTP classifier: code first, status second; 408/429/5xx and
connection faults retryable; other 4xx fatal); `Retry` (`max_attempts` [1,10]=3,
`base_s`=0.05, `throttle_base_s`=1.0, `cap_s ≤ MAX_BACKOFF_S`=20, `jitter ∈ {full,
equal, none}`, `retry_after ∈ {honor, ignore}` always capped, `retry_writes ∈
{never, idempotent_only}`, `budget{capacity 500, transient_cost 14,
throttle_cost 5, refund 1}`; `decide(attempt, kind, is_write) ∈ {retry, give_up,
reconcile}`; an ambiguous write never yields `retry`); `CircuitBreaker` (states
`closed | open | half_open | forced_open | metrics_only`; `trip`/`reset`; one per
scope; `min_calls`=5; `failure_rate`=0.5; `open_s`=30; business rejections not
counted); `RateLimiter` (token buckets per scope, `max_in_flight` default 1 for
writes, `observe(headers)` capped by `MAX_BACKOFF_S`); `Transport(ABC)`
(`send(method, url, headers, body, timeout{connect_s, read_s}) -> (status, headers,
body)`; `UrllibTransport`; `None` timeout refused). All take injected `clock`,
`sleeper`, `rng`. Phase 2: `Signer(ABC)` with `HmacSigner`, skew window, time
probe.

### 5.13 `loop.py`, `outcomes.py`, `report.py`, `readiness.py`

- `ServeLoop(document, clock, calendar, cadence, feed, decider, guards, breaker,
  arming, executor, ledger, monitors, alerts, health, heartbeat)`: lifecycle
  `init → locked → reconciling → ready → {waiting ⇄ ticking} → stopping →
  stopped`, plus `halted` (persisted; refuses restart until `resume`) and
  `faulted` (restartable). Tick phases as methods on an abstract `Tick` (subclass
  to extend; none branch on mode): `gate` (calendar; closed ⇒ `skipped:closed`),
  `fetch` (feed), `decide` (decider), `watermark` (`tick_at − data_asof_ms >
  max_staleness_ms` ⇒ `skipped:stale`; venue skew ⇒ `skipped:skew`), `guard`,
  `record_intent` (durable before I/O; `client_ref = uuid4`; `intent` record),
  `act` (`executor.submit`), `record_outcome` (`acked | rejected | unknown` +
  `order_event`), `observe` (monitors, health, heartbeat, alerts), `checkpoint`
  (last, atomic). Exactly one `tick` record per tick, written in `finally`, with
  `status ∈ {decided, skipped:closed, skipped:stale, skipped:skew, skipped:halted,
  skipped:degraded, skipped:no_coverage, refused, failed}` and every guard
  finding. Exit codes: 0 stopped · 1 error · 3 halted · 4 already running.
  `--once` runs one tick; `--max-ticks N`.
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

Envelope on every record: `kind, id, seq, process_id, recorded_at_ms,
schema_version, prev_hash, hash`. Ids are positional (`tick_id = process_id:seq`,
`decision_id`, `leg_id = decision_id/instrument`, `client_ref` uuid4); digests are
content-addressed with the existing sha256-canonical idiom.

| kind | one per | body |
|---|---|---|
| `process` | start / stop / recovered | `doc_hash`, `serving_hash`, `run_hash`, `artifact_digests`, `rung`, `executor_kind`, `code_version`, `clock`, `journal_action_id`; on stop `head_seq`, `head_hash`, `exit_code` |
| `tick` | tick | `tick_at`, `data_asof_ms`, `observed_at_ms`, `status`, `feed{status, acq_id, records_added}`, `inputs_digest`, `calendar`, `overrun_absorbed[]`, `latency_ms{fetch, decide, guard, act}`, `health`, `breaker`, `rung`, `refusal_reason`, `error{class, text}` |
| `decision` | tick (exactly one) | `legs[]{leg_id, instrument, prediction, uncertainty, baseline, expected_value, decision_price, proposal, findings[], final, client_ref}` — a no-op tick has `final: none` per leg or zero legs with `reason` |
| `intent` | submitted proposal | `client_ref`, `leg_id`, `proposal (final)`, `created_ms`, `arming_id` |
| `order_event` | executor report | `client_ref`, `venue_ref`, `event ∈ {ack, reject, fill, partial_fill, cancel, expire, replace, unknown, status}`, `status`, `venue_ts_ms`, `recv_at_ms`, `reason` |
| `fill` | execution | the `Fill` record |
| `outcome` | label arrival / mark / correction | `leg_id`, `kind ∈ {settled, marked, voided, partial, corrected}`, `effective_at_ms`, `known_at_ms`, `value`, `weight`, `terminal`, `supersedes`, `source` |
| `recon` | reconciliation run | `scope`, `ours_digest`, `theirs_digest`, `breaks[]`, `status`, `action` |
| `trip` | breaker transition | `from`, `to`, `reason`, `actor`, `cancel_outcome` |
| `arming` | arm / disarm | the `Arming` record + `actor` |
| `monitor` | verdict change / window close | `monitor`, `slice`, `window`, `statistic`, `threshold`, `status`, `provisional` |
| `alert` | firing / resolved | the `Alert` record + per-sink outcomes |
| `health` | transition | `from`, `to`, `cause`, `probe_evidence` |
| `snapshot` | every N records | `at_seq`, `state_digest`, `state` (positions, open orders, pending refs, monitor states) |

## 7. CLI — `python -m dskit.production`

| verb | does | exit |
|---|---|---|
| `validate <doc>` | shape + identity hash | 0 / 1 |
| `plan <doc> [--adapter]` | derive the serving document, print the needed subgraph, executor kind, guards, calendar; refuse an unserveable document | 0 / 1 |
| `serve <doc> [--adapter] [--once] [--max-ticks N] [--armed] [--adopt]` | the loop | 0 / 1 / 3 / 4 |
| `arm <doc> --rung R --by P [--by P] --until TS [--allow I]…` / `disarm <doc>` | arming records (+ journal rows) | 0 / 1 |
| `halt <doc> --reason` / `resume <doc> --acknowledge TRIP` | breaker (+ journal rows) | 0 / 1 |
| `status <doc>` | rung, breaker, health, last tick, pending refs, head hash | 0 |
| `verify <doc>` | walk the ledger chain; compare the head to the journal anchor | 0 / 1 |
| `reconcile <doc>` | one reconciliation run | 0 / 3 |
| `replay <serve-dir> [--adapter]` | phase 2: parity report | 0 / 1 |
| `outcomes <doc>` / `report <doc> [--asof T]` | phase 2 | 0 |
| `ready <doc>` | phase 2: readiness GO / NO-GO | 0 / 3 |

Only operational flags live on the CLI (`--once`, `--max-ticks`, `--adopt`,
`--armed`, `--adapter`). No knob of the run or the serve document is restated.

## 8. Package structure — file by file

```
dskit/production/
├── __init__.py        public surface (curated re-exports only, no logic)
├── __main__.py        CLI: validate | plan | serve | arm | disarm | halt | resume | status | verify | reconcile | replay | outcomes | report | ready
├── base.py            ProductionError; checkers re-exported from dskit.assets.base; ms/utc helpers; canonical record hashing
├── vocab.py           EVERY closed vocabulary, one module: RUNGS, VERDICTS (lattice), STATUSES + TERMINAL, TIFS, SIDES, FILL_STATUSES,
│                      SEVERITIES (+ the pinned level map), HEALTH_STATES, BREAKER_STATES, LOOP_STATES, TICK_STATUSES, RECORD_KINDS,
│                      BREAK_CLASSES, DIVERGENCE_CLASSES, MONITOR_STATUSES, RESPONSES, FEED_STATUSES, OUTCOME_KINDS
├── document.py        ServeDocument + section dataclasses (Serving, Entry, Feed, Schedule, Guards, Execution, Reconcile, Monitors,
│                      Health, Heartbeat, Alerts, Ledger); from_obj default-deny; to_obj; identity hash; NON_IDENTITY_SECTIONS; load/save
├── records.py         Quote, Proposal, Finding, Intent, Ack, OrderState, Fill, Position, Balance, Settlement, Alert, Verdict, Profile
├── clock.py           Clock ABC; WallClock, TestClock, ReplayClock
├── sessions.py        Calendar ABC; AlwaysOpen, WeeklySessions, EventWindow, Composite; CALENDAR_KINDS
├── cadence.py         Cadence ABC; FixedInterval, AlignedBar, AtTimes, OnData; Overrun; CADENCE_KINDS
├── feed.py            Feed ABC; AcquireFeed, StoreFeed, ReplayFeed; FeedResult; FEED_KINDS
├── decider.py         serving_document(); Decider (base pass + per-tick rerun via SubgraphRunner); Proposer ABC; IntentRows,
│                      TargetPositions; RecordedOutputs (replayed gate / stat_test); PROPOSER_KINDS
├── guards.py          Guard ABC; Finding lattice; GuardChain; Limit; RangeGuard; Measure ABC + MEASURE_KINDS; windows; GUARD_KINDS
├── breaker.py         Breaker (active | reducing | halted), persisted; trips; kill-switch file; cooling-off
├── arming.py          Arming record; arm / disarm / current / check_conjunction; NotArmed
├── executor.py        Executor ABC; LiveExecutor ABC (arming precondition); PositionBook; ShadowExecutor; PaperExecutor (+ fill/fee
│                      strategies); RecordedExecutor; executor_conformance_suite; EXECUTOR_KINDS
├── resilience.py      Classifier ABC + HttpClassifier; Retry (+ budget); CircuitBreaker; RateLimiter; Transport ABC + UrllibTransport
├── ledger.py          Ledger ABC; JsonlLedger; Checkpoint; ServeRoot (layout + lock); record envelope + chain + verify
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
└── CLAUDE.md          conventions, extension points, gotchas; tree

tests/production/
├── conftest.py            a synthetic training run (the pipeline's synthetic kinds + an observations-reading data node over a temp
│                          onboarding root), a TestClock, a MemorySink — every test builds on these, no network anywhere
├── test_purity.py         static + behavioural: stdlib + dskit.pipeline + dskit.onboarding + dskit.assets + self; journal function-import only;
│                          no `mode ==` / `rung ==` branch in loop.py
├── test_vocab.py          every vocabulary closed; the severity level map pinned; lattice order pinned
├── test_document.py       default-deny at every level; golden identity hash; non-identity exclusion; omission discipline; load/save round trip
├── test_records.py        Decimal at the boundary; non-finite refused; round trips
├── test_clock.py          TestClock determinism; WallClock honours the stop flag; monotonic pacing survives a wall-clock jump
├── test_sessions.py       weekly sessions across spring-forward and fall-back; holidays, special closes, blackouts, buffers; DST-gap refusal
├── test_cadence.py        FixedInterval zero drift over 10^6 ticks with slow handlers; AlignedBar publish delay; AtTimes; overrun policies
├── test_feed.py           StoreFeed staleness; AcquireFeed through a FakeConnector root; ReplayFeed drives the ReplayClock
├── test_decider.py        serving_document derivation (mode flip, artifact pins, winner applied, search dropped, foreach expanded, $prev refused,
│                          cut to ancestors); rerun on the synthetic run; watermark = entry data_edge; no-restatement pin
├── test_guards.py         one refusal per knob; lattice composite = max; every finding carries value/bound/reason; include_working; calendar
│                          windows; amend never exceeds bound; hypothesis: day_loss halts before the period loss exceeds bound − max single loss;
│                          cancels bypass the chain
├── test_breaker.py        trips persist across restart; reset refused before cooling-off / without a trip id; kill-switch file; halt cancels
├── test_arming.py         two distinct principals for ≥ live_limited; expiry per tick; hash mismatch refuses; conjunction; disarm always succeeds
├── test_executor.py       the 15-item conformance battery on Shadow, Paper, Recorded; paper determinism; ioc/fok/gtd; fee closed forms; NotArmed
├── test_resilience.py     jitter with injected rng; ambiguous write never retries; budget; breaker per scope; limiter caps header hints
├── test_ledger.py         torn tail; chain edit/delete/insert/reorder located; idempotent append; rotation continuity; flock; checkpoint atomicity;
│                          crash subprocess mid-batch
├── test_reconcile.py      every break class classified; on_mismatch policies; pending refs resolved; external orders never adopted; start refused on block
├── test_monitors.py       PSI = 0 on identical samples and χ² scaling; KS hand case; PageHinkley alarms on a shift, not on noise; tracking signal;
│                          insufficient below min_n; last partial chunk never ok; state round-trip; deterministic verdict records
├── test_alerts.py         sink failure never kills the loop; hanging sink bounded (never-replying local socket); construction refusal; dedup /
│                          group_wait / repeat / rate limit / critical bypass; resolved emitted
├── test_health.py         transitions and hysteresis; unhealthy stops heartbeating; heartbeat rid = tick id; second instance exits 4; SIGTERM
├── test_loop.py           phase order; one tick record per tick including crashed ticks; crash injection at every phase boundary then restart ⇒
│                          exactly one venue action per client_ref; skipped:* statuses; exit codes; journal row once per process with head anchor
├── test_main.py           CLI e2e: validate / plan / serve --once (shadow, then paper) over the synthetic run; arm / disarm / halt / resume / status / verify
├── test_outcomes.py       [phase 2] forward join strictness (hypothesis: label asof > decided_at); vintage reproducibility; supersede chain
├── test_report.py         [phase 2] IS components sum; Murphy terms sum to Brier; BSS of baseline vs itself = 0; parity diff classes
└── test_readiness.py      [phase 2] NO-GO exits 3; waivers

examples/production/
├── serve-shadow.json      the synthetic run served at shadow — the 60-second path
├── serve-paper.json       the same with the paper executor and basic guards
└── calendar-weekly.json   a weekly-sessions calendar with holidays and buffers
```

## 9. Changes outside the package

### 9.1 Pipeline — ADR-0083: the subgraph re-execution seam becomes public

`dskit/pipeline/driver.py`: extract `_SearchSeam._execute` into a public
`SubgraphRunner(plan, node_outputs, splits_info, prev, policy)` with
`rerun(overrides, ctx, bindings=None, guard_verdicts=False) -> (reran, seconds)`,
and a classmethod `prepare(document, asof, registry, run_dir, heads)` that runs
LOAD → PLAN → RESOLVE (through `_resolve_run`, so the base pass has a real run
dir with `config.json`, `plan.json`, `resolved.json`) and executes `ancestors(heads)
∪ heads` once (a search node in that set refuses). `policy` is an
`OverridePolicy` object with `refuse_reason(role, head_param) -> str | None`:
`SearchOverridePolicy` wraps `unsearchable_space_why` (today's rule, verbatim);
`ServingOverridePolicy(entry_node, entry_param)` permits exactly one address.
`_SearchSeam` keeps `needed`, `dirty`, the objective dig and `calls`, and
delegates execution — behaviour-neutral for search, pinned by the existing
`test_driver.py` / `test_kinds_search.py` suites and by every identity hash in
the repo (218 + 2 pinned) staying unmoved. No new params, no grammar change.

### 9.2 Skeleton (pin updated in the same commit)

`children/_skeleton/yourproject/execution.py` (a `LiveExecutor` template:
`spec()`, `capabilities()`, refusing verbs, `NotArmed` by construction),
`configs/serve-sample.json` (serves `run-sample.json` at shadow with the
`intent-rows` proposer), `tests/test_execution.py` (the conformance battery on
the template + `serve-sample.json` validates and plans). `children/README.md`,
the skeleton `README.md` / `CLAUDE.md` gain a "Going to production" section:
`python -m dskit.production serve configs/serve-sample.json --adapter
yourproject --once`. Existing children are not rewritten by this ADR (§12.4).

### 9.3 Documentation and configuration

Root `README.md` (a fifth pillar, "Serve"), root `CLAUDE.md` (layout tree,
commands, the exit-code line), `docs/architecture/README.md` (package table),
`TODO.md` (the "Long-term goal — a generic SERVING LOOP" section marked
superseded by ADR-0082 with its constraints listed as satisfied), `pyproject.toml`
(no new extras in phase 1; the new modules follow the docstring standard, so no
`per-file-ignores` entries), `docs/architecture/decision-log.md` (ADR-0082,
ADR-0083).

## 10. Test plan (TDD)

Order per module: the Opus test author writes `tests/production/test_<module>.py`
from §5–§7 (contracts, vocabularies, bounds, invariants) — red; Fable implements
`dskit/production/<module>.py` — green; Opus reviews the pair. Module order
follows dependencies: `vocab` → `base` → `records` → `document` → `clock` →
`sessions` → `cadence` → `ledger` → `guards` → `breaker` → `arming` → `executor`
→ `resilience` → `feed` → (pipeline `SubgraphRunner`) → `decider` → `reconcile`
→ `monitors` → `alerts` → `health` → `loop` → `__main__` → docs → examples →
skeleton.

Invariants every phase must keep green: the three existing purity gates and the
new one; every pinned identity hash unmoved (218 pipeline + 2 pmquant); the full
suite; `ruff` clean; the skeleton pin.

The e2e (in `conftest.py`): build a synthetic run with `run_document` over a temp
onboarding root filled by the `FakeConnector`; serve it for three ticks at
`shadow` with `TestClock`, then at `paper`; assert one `tick` + one `decision`
per tick, the chain verifies, the journal row exists with the head anchor, and
replaying the three ticks reproduces byte-identical decision records.

## 11. Build phases and model assignment

| phase | lands | model |
|---|---|---|
| 1 — foundation | every module in §8 not marked phase 2, ADR-0083, the CLI verbs through `reconcile`, README/CLAUDE, examples, skeleton, doc updates | tests: Opus · code: Fable · review: Opus |
| 2 — evidence | `outcomes`, `report`, `readiness`, `replay` verb, Outcome + Parity monitor families, DDM/ADWIN/JS/Linf, alert inhibition/silences/escalation/ack + sqlite state, systemd heartbeat, `libs/sqlite.py`, `libs/parquet.py`, `Signer`, the `approve` verb for `hold` | tests: Opus · code: Opus · review: Opus |
| 3 — packs | `exchange_calendars`, `prometheus`/`opentelemetry` sinks, the stream seam, migrating onboarding packs onto `resilience.py` (own ADR) | Opus |

Per the owner: no Fable after the initial plan and build.

## 12. Open choices for the owner

1. **Cadence home** (D5): (A) keep the pipeline's `clock`/`schedule` as
   documentation and put cadence in the serve document — recommended; (B) give
   the pipeline `clock` section execution meaning and have the serve document
   reference it.
2. **Package name**: (A) `dskit.production` — recommended, matches the journal
   category and the owner's words; (B) `dskit.serving`; (C) `dskit.live`.
3. **Replayed verdict nodes** (§5.3): (A) replay gate/stat_test outputs from the
   run's records through `RecordedOutputs` — recommended; (B) refuse any needed
   gate/stat_test node and require the child's serving head to sit above them.
4. **Existing children**: (A) leave `intraday_poc/live.py` and
   `intraday_equities/live.py` as they are; port `intraday_poc` onto
   `dskit.production` as the phase-2 acid test in its own child commit —
   recommended; (B) port both in phase 1; (C) never port.
5. **Ledger default**: (A) JSONL in phase 1, sqlite pack in phase 2 —
   recommended; (B) sqlite first.
6. **Arming principals**: (A) two distinct principals from `live_limited` up,
   one for `paper` — recommended; (B) two for every rung above shadow.

## 13. Traceability

| piece | grounded by |
|---|---|
| loop lifecycle, three timestamps, checkpoint-last, unknown-outcome rule, lock, signals, exit codes, replay tape | R7 |
| seven-box decomposition, swap-three symmetry, refusal as recorded status, restart amnesia | R9 |
| executor verbs, status vocabulary, fills not final, units, paper knobs, conformance battery | R8 |
| order state machine, gate taxonomy, reconciliation loop, arming three-way agreement, postmortems | R1 |
| guard lattice, `Limit` = measure × window × bound × scope, mode ladder vs breaker, halt ≠ flatten | R5 |
| ledger kinds, envelope, hash chain + journal anchor, JSONL/sqlite contract, bitemporal outcomes, attribution, parity diff, venue reconciliation job | R6 |
| retry classification, ambiguous writes reconcile, breaker per scope, rate limiter, transport timeouts, live conjunction | R3 |
| symptom alerts, severity map, sink discipline, router, health model, heartbeat, one record per tick, readiness | R4 |
| monitor ABC and families, references/chunkers/thresholds, `insufficient`, release ladder | R2 |
| the constraints every piece must satisfy | the owner's `TODO.md` serving-loop section; root `CLAUDE.md` |

---

## Appendix A — ADR-0082 as it will appear in `decision-log.md`

```
## ADR-0082 — `dskit.production`: the production layer (serve, guard, act, record, monitor)

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
with its own identity; one `ServeLoop` with injected clock / calendar / cadence /
feed / decider / guards / breaker / arming / executor / ledger / monitors / alerts /
health; the decision is a re-execution of the run's own subgraph in load mode
through the public seam of ADR-0083; guards as a verdict lattice over one `Limit`
family; rung, breaker, and health as three orthogonal permissions; arming as a
recorded, expiring, two-principal act bound to the document hash; an append-only
hash-chained ledger anchored into the journal; shadow / paper / recorded executors
in core and a conformance battery for children's; monitors, alerts, health, and
resilience policies as strategy objects. Purity: stdlib + pipeline + onboarding +
assets + self; journal function-import only.

**Consequences.** Children stop owning loops: a child ships an executor subclass,
optionally a proposer and measures, and JSON. The skeleton gains execution.py,
serve-sample.json, test_execution.py (pin updated). TODO's serving-loop section
is superseded. Phase 1 lands the foundation (tests Opus, code Fable, review Opus);
phases 2–3 (evidence, packs) are Opus. Open choices §12 ruled by the owner.
```

## Appendix B — ADR-0083 as it will appear

```
## ADR-0083 — The driver's subgraph re-execution is a public seam with a policy object

**Status:** proposed (2026-09-04)

**Context.** `_SearchSeam._execute` already re-executes `needed ∩ dirty` under
`"node.param.path"` overrides with the full node lifecycle, but it is private,
returns an objective float, and hardcodes the search override rule. A serving loop
needs the same mechanism with one different override rule and head outputs.

**Decision.** `SubgraphRunner` (public, `driver.py`) with `prepare(document, asof,
registry, run_dir, heads)` (LOAD → PLAN → RESOLVE → base pass over
`ancestors(heads) ∪ heads`; search nodes refused) and `rerun(overrides, ctx)`.
`OverridePolicy.refuse_reason(role, head_param)`: `SearchOverridePolicy` wraps
`unsearchable_space_why` verbatim; `ServingOverridePolicy` permits exactly one
declared address. `_SearchSeam` delegates; no grammar, param, or hash changes.

**Consequences.** One mechanism, two callers; the search suites pin
behaviour-neutrality; every identity hash stays unmoved.
```
