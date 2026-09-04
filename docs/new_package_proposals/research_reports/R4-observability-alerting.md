# R4 — Observability, alerting, health, and operational readiness for an unattended decision loop

Research input for the `dskit.production` design (working name). Scope: a process that
polls data, decides, acts through an API, and runs unattended — trading, prediction
markets, betting, or any non-financial decision system. Everything below is
domain-neutral; project specifics are config, never code.

Repository precedent this must fit: the `Tracker` protocol (`log_params`,
`log_metrics(stage, mapping)`, `close`) with sinks registered by kind; the driver
swallows sink exceptions, so a sink must **validate loudly at construction** and
**bound its own remote calls** (`dskit/pipeline/libs/mlflow.py` probes reachability with
`connect_timeout`, default 5 s, at plan time). `dskit.journal.hooks.production()` records
**one row per process, never per tick** (ADR-0056) — so per-tick records are a new,
separate artifact. Exit code **3** already means "halted at a gate; a halt is a result".

---

## 1. Findings by theme

### 1.1 Google SRE: golden signals, SLOs, symptom-based paging

- **Four golden signals** (SRE book, "Monitoring Distributed Systems"): latency ("the time
  it takes to service a request" — track successful and failed latency separately),
  traffic ("how much demand is being placed on your system"), errors ("the rate of requests
  that fail, either explicitly ..., implicitly ..., or by policy"), saturation ("how 'full'
  your service is"). Mapped to a decision loop: latency = tick / decision / submit
  durations; traffic = ticks, decisions and acts per period; errors = failed ticks,
  refusals by reason, rejected acts, reconciliation diffs; saturation = exposure vs limit,
  rate-limit headroom, queue depth, error-budget burn.
- **Symptoms vs causes.** "What versus why is one of the most important distinctions in
  writing good monitoring with maximum signal and minimum noise." Rob Ewaschuk's
  "My Philosophy on Alerting" (encoded into the SRE book and the Prometheus alerting
  guide): "Pages should be urgent, important, actionable, and real"; "Err on the side of
  removing noisy alerts — over-monitoring is a harder problem to solve than
  under-monitoring"; alert on the symptom, keep cause-based alerts for imminent
  exhaustion; track page accuracy and reconsider anything below ~90%.
- **Page rules** (SRE book): "Every page should be actionable"; "Every page response
  should require intelligence. If a page merely merits a robotic response, it shouldn't
  be a page"; pages should be novel. Questions to ask per rule: detects an urgent,
  actionable, user-visible condition? will you ever ignore it as benign? is there an
  action?
- **Practical alerting** (SRE book, Borgmon): alerts "can 'flap'", so rules require a
  minimum duration — "at least two rule evaluation cycles"; a rule combines a threshold,
  a minimum count ("total number of errors exceeds 1 per second") and a hold ("for 2m").
  Tiering: page-worthy → on-call; "important but subcritical" → ticket queue; the rest →
  dashboards.
- **Batch / offline guidance** (Prometheus practices): "For batch jobs it makes sense to
  page if the batch job has not succeeded recently enough"; set the threshold at ≥ 2×
  the typical run time. "For offline processing systems, the key metric is how long data
  takes to get through the system." Metamonitoring: a black-box test of the whole
  alerting path, not per-component alerts.
- **SLO alerting** (SRE workbook, "Alerting on SLOs"): evaluate rules on precision,
  recall, detection time, reset time. Naive "error rate ≥ SLO over 10 min" can fire "up
  to 144 alerts per day ... and still meet the SLO". The recommended design is
  **multiwindow, multi-burn-rate**: page at burn rate 14.4 over 1 h (short 5 min) and 6
  over 6 h (short 30 min); ticket at burn rate 1 over 3 d (short 6 h). **Low-traffic
  services** break this: "If a system receives 10 requests per hour, then a single failed
  request results in an hourly error rate of 10%" — remedies are synthetic traffic,
  aggregation, or SLOs "commensurate with actual impact". A decision loop that ticks a
  few times an hour *is* a low-traffic service; count-based windows ("k of the last n
  ticks") are more honest than rate windows. "We strongly advise against specifying the
  alert window and burn rate parameters independently for each service" — use a few
  shared buckets.
- **SLI menu** (workbook, "Implementing SLOs"): request-driven — availability, latency,
  quality; data processing — **freshness** ("proportion of the data that was updated more
  recently than some time threshold"), **correctness**, **coverage**, throughput. Pipelines
  chapter: "Stale data is almost always better than incorrect data"; every alert gets "a
  corresponding playbook entry that describes the steps to recovery"; idempotent
  mutations; **two-phase mutations** (stage, verify, then apply); checkpointing.
- **On-call load** (SRE book / workbook): at most "2 per 12-hour on-call shift", median
  ideally 0; "a 1:1 alert/incident ratio" via grouping; "Playbooks contain high-level
  instructions on how to respond to automated alerts. They explain the severity and
  impact of the alert, and include debugging suggestions and possible actions"; "If your
  playbooks are a deterministic list of commands ..., we recommend implementing
  automation." Fatigue is real: SOC surveys report ~60% of alerts ignored and >50% false
  positives (security-domain numbers, indicative only).
- **Monitoring strategy** (workbook, "Monitoring"): data "more than four to five minutes
  stale might significantly impact how quickly you can respond"; percentiles at record
  time; "Treating monitoring configuration as code"; loose coupling of collection,
  storage, alerting, display; "Each exposed metric should serve a purpose" — intended
  changes, dependencies, saturation, served-traffic status.

### 1.2 Health model: startup / liveness / readiness, heartbeats, watchdogs, staleness

- **Kubernetes probes**: startup ("verify whether the application within a container is
  started"; liveness/readiness wait for it), liveness ("determine when to restart a
  container" — deadlock is the canonical case), readiness ("determine when a container
  is ready to accept traffic"; failure removes it from service without restart).
  Defaults: `periodSeconds` 10, `timeoutSeconds` 1, `failureThreshold` 3,
  `successThreshold` 1. Warning: liveness probes "must be configured carefully to ensure
  that they truly indicate unrecoverable application failure ... Incorrect implementation
  ... can lead to cascading failures." Common pattern: same cheap endpoint for both, with
  a higher failure threshold for liveness so a process is seen as not-ready before it is
  killed. "If the process ... is able to crash on its own ... you do not necessarily
  need a liveness probe."
- **Amazon Builders' Library, "Implementing health checks"**: four kinds — liveness
  (port/200), **local** (disk writable, support processes, "unlikely to fail on many
  servers ... simultaneously"), **dependency** (config staleness, peers, credentials —
  "can also have false positives when there are problems with the dependency itself"),
  **anomaly** (clock skew, old code, "any unanticipated failure mode"). Reaction policy:
  fast automation only on local checks; dependency failures go to "a central authority"
  or a human; **fail open** when everything looks unhealthy; "prioritize their health
  checks over their regular work"; run dependency checks in a background thread updating
  an `isHealthy` flag — and detect that thread dying. A health-checked soft dependency
  becomes a hard dependency: "If the dependency is down, the service also goes down."
- **Dead-man's switch / heartbeat** (healthchecks.io): the process pings on success;
  the *monitor* alerts when the ping stops. States: new, up, **late** (due but within
  grace), **down** (grace elapsed), paused. Knobs: **period** ("expected time between
  pings") and **grace** ("additional time to wait before sending an alert when a check is
  late"; also the max start→success gap). Ping API: plain HTTP GET/POST/HEAD to a URL,
  `/start`, `/fail`, `/log`, `/<exit-status>`, optional `rid` (client-chosen run UUID to
  pair start/finish), body kept up to 100 kB, ≤ 5 pings/min. Prometheus practice:
  an always-firing **Watchdog** alert routed to an external snitch proves the whole
  pipeline end-to-end; if the alert stops arriving, "sound the horn".
- **systemd watchdog**: `WatchdogSec=` requires `WATCHDOG=1` keep-alives within the
  interval or the unit gets `SIGABRT` and restarts under `Restart=on-watchdog`; the
  protocol is newline-separated assignments (`READY=1`, `STATUS=...`, `WATCHDOG=1`,
  `STOPPING=1`) written to the `$NOTIFY_SOCKET` AF_UNIX datagram socket — implementable
  with stdlib `socket`.
- **Azure health modeling**: three states — **Healthy / Degraded / Unhealthy** — derived
  from signals + thresholds; "A health model shouldn't treat all failures the same. It
  should clearly distinguish between expected or transient but recoverable failures and a
  true disaster state"; alert on health-state changes rather than raw signals ("separation
  between monitoring data and alert rules"); "Separate application logging from
  auditing"; "Use asynchronous logging".

### 1.3 Structured logging and audit trails

- **Amazon, "Instrumenting distributed systems"**: "Emit one request log entry for every
  unit of work" and "no more than one" — plumb a single metrics object through the
  stages and serialize at the end (long tasks may emit periodic progress entries). Put
  counters, timers and properties in it; "Record details about the request before doing
  stuff like validation"; sanitize and truncate inputs (log injection, disk fill); keep a
  verbosity knob; separate the structured request log from the debug log; "Log the
  availability and latency of all dependencies", per call and per status code; "Add an
  additional counter for every error reason" and separate client-fault from
  server-fault; log "enough metadata ... to determine whom the request was from and what
  ... it was attempting to do" but not payloads; decide loggable fields "in an opt-in
  way instead of an opt-out way"; propagate a trace id by passing a context object;
  separate success-latency from error-latency timers; offload flushing to another thread;
  "Consider the behavior of the system when disks fill up"; synchronize clocks; emit zero
  counts so silence is detectable.
- **OWASP Logging Cheat Sheet**: record when / where / who / what; never log session ids,
  access tokens, passwords, connection strings, keys, card data; "Build in tamper
  detection"; "Store or copy log data to read-only media as soon as possible";
  synchronize time; ensure "failures in the logging processes/systems do not prevent the
  application from otherwise running".
- **OpenTelemetry log data model**: `Timestamp`, `ObservedTimestamp`, `TraceId`,
  `SpanId`, `SeverityText`, **`SeverityNumber` 1–24** (TRACE 1–4, DEBUG 5–8, INFO 9–12,
  WARN 13–16, ERROR 17–20, FATAL 21–24) so severities compare across sources, `Body`,
  `Attributes`, `Resource`. **W3C Trace Context**: trace-id = 16 random bytes (32 hex),
  span-id = 8 bytes; all-zero invalid.
- **Python stdlib**: `logging.handlers.QueueHandler` + `QueueListener` move slow
  handlers off the hot path ("almost any network-based handler can block");
  `SMTPHandler(..., timeout=1.0)` has a timeout, **`HTTPHandler` has none** (avoid);
  `RotatingFileHandler(maxBytes, backupCount)`, `TimedRotatingFileHandler(when, interval,
  backupCount, utc)`; `contextvars` + a `Filter` inject a per-tick id into every record;
  `smtplib.SMTP(host, port, timeout=)` raises `TimeoutError`, `urllib.request.urlopen(...,
  timeout=)` likewise.
- **Tamper-evident ledger**: each row stores a monotonic sequence, `prev_hash`, and
  `hash = sha256(prev_hash ‖ seq ‖ canonical JSON of the semantic fields)`; storage is
  insert-only; verification "walks the chain, recomputes every hash, reports any breaks";
  anchor periodic heads somewhere the writer cannot alter (mailing the daily head hash to
  the alert address is a stdlib anchor). Retention is policy-driven (NIST SP 800-92:
  "ensuring that records are stored for the required period of time"; regulated domains
  prescribe multi-year retention) — a config knob, not a constant.
- **Idempotency** (Stripe): a client-generated key (UUID v4) lets a request be retried
  "without risk of creating a second object"; the server stores the first outcome, "errors
  if [parameters are] not the same", keys expire after 24 h. Amazon: "APIs with side
  effects aren't safe to retry unless they provide idempotency."

### 1.4 Metrics design

- **Prometheus naming**: single-word application prefix; **base units** (seconds, bytes);
  unit suffix, `_total` for accumulating counts; a metric "SHOULD represent the same
  logical thing-being-measured across all label dimensions" ("either the `sum()` or the
  `avg()` ... should be meaningful"); "Do not put the label names in the metric name";
  "Do not use labels to store dimensions with high cardinality ... such as user IDs,
  email addresses, or other unbounded sets" — every label combination is a new series.
  Histograms aggregate server-side; summaries do not. Push only "in certain limited
  cases" (service-level batch jobs): a Pushgateway "never forgets series", loses the `up`
  health signal, and is a single point of failure. `prometheus_client`: `Counter`,
  `Gauge`, `Histogram`, `Summary`, `Info`, `Enum`, `start_http_server`,
  `push_to_gateway(..., timeout=30)`.
- **What to measure for a decision loop** (synthesis of §1.1–1.3): tick duration and
  result; data age (now − newest observation) and last-success age; decision and submit
  latency (success and failure timed separately); act outcomes; refusal counts by a
  **closed** reason set; guard trips; reconciliation diffs (expected vs observed state —
  the controller pattern: "make or request changes ... to move the current state closer
  to the desired state"); open exposure and its fraction of the limit; realized /
  unrealized score; dependency call latency and error reasons per dependency; retry
  counts (Amazon: "we maintain metrics that monitor overall retry rates"); alert-sink
  successes/failures/suppressions; heartbeat sends; kill-switch state; mode.

### 1.5 Alert routing sinks, dedup, rate limits, escalation, acknowledgement

- **PagerDuty Events API v2**: `event_action` ∈ {trigger, acknowledge, resolve};
  `dedup_key` — "subsequent alerts with a matching dedup_key deduplicate into the same
  incident"; after resolve a new trigger opens a new alert; **`severity` ∈ {critical,
  error, warning, info}** is required; payload needs `summary`, `source`, `severity`.
- **Slack incoming webhooks**: POST JSON `{"text": ...}` to a per-channel URL; "Your
  webhook URL contains a secret"; errors 400/403/404 with `invalid_payload`, `no_text`,
  `channel_is_archived`. **Discord**: ~30 requests/min per webhook, `429` with
  `retry_after`. Both are plain HTTPS JSON — stdlib `urllib` suffices.
- **Alertmanager semantics** worth copying: `group_wait` (default 30 s) before the first
  notification of a new group, `group_interval` (5 m) between updates, `repeat_interval`
  (4 h) before re-sending an unchanged alert; dedup by a fingerprint of the label set;
  **inhibition** (mute targets when a source alert with equal labels fires); **silences**
  by matcher and time window; webhook payload carries `status: firing|resolved`.
- **Email**: `smtplib` with `timeout`, `SMTP_SSL` or `starttls`, context manager issues
  `QUIT`; exceptions form the `SMTPException` hierarchy (`SMTPRecipientsRefused`, etc.).

### 1.6 Production-readiness / operational-readiness reviews

- **Google PRR**: verify "a service meets accepted standards of production setup and
  operational readiness"; the checklist covers architecture and dependencies,
  instrumentation and metrics, monitoring and alerting, emergency response, capacity,
  change management, performance, operational controls, owner preparation; incidents and
  postmortems feed the checklist; engage early, not after launch.
- **AWS ORR**: "An ORR is two things: a process and a checklist"; run "before a workload
  launches to general availability and then throughout the software development
  lifecycle"; anti-patterns: "You launch a workload without knowing if you can operate
  it", "Workloads launch without required procedures in place". Example questions: blast
  radius; a **failure model** table (component, failure type, service impact, customer
  impact); retry/back-off per dependency; "intentionally set appropriate retry and socket
  timeout configuration"; RTO for restart with a runbook and no circular dependencies;
  automatic rollback on alarm; on-host validation before taking traffic; changes reviewed
  by someone other than the author; a **game day** proving alarms and on-call engage;
  canary errors on their own alarmed metric, detected "in under five minutes"; P50/P99/
  P99.9; dashboards with dependency metrics; a weekly ops review agenda.
- **Runbook/playbook anatomy** (industry templates): alert meaning and severity, first
  five minutes, dashboard links pre-filtered, escalation path, top mitigations with exact
  commands, communication templates, postmortem trigger criteria.
- **Regulated-domain evidence for a kill switch**: MiFID II RTS 6 Art. 12 — a firm "shall
  be able to cancel immediately, as an emergency measure, any or all of its unexecuted
  orders" and identify which algorithm is responsible for each order; SEC Rule 15c3-5 —
  pre-set credit/capital thresholds, with a documented rationale and monitoring of their
  continued appropriateness. The neutral generalization: a kill switch both **stops new
  acts** and **cancels/unwinds pending ones**, every act is attributable to a decision
  and a config identity, and limits are documented, not tacit.

### 1.7 Graceful degradation, feature flags, safe defaults

- **Feature toggles** (Fowler): **ops toggles** are "manual circuit breakers" and
  long-lived **kill switches**; "prefer static configuration"; decouple the toggle point
  from the decision; inject decisions at construction; prefer Strategy over if/else;
  toggles are "inventory which comes with a carrying cost"; convention: off = existing
  behaviour, on = new behaviour.
- **Circuit breaker** (Azure Architecture Center): closed / open / half-open; a
  time-based failure counter; "provide a manual reset option"; raise an event on each
  state change; the open state "can return a default value that's meaningful"; beware
  long dependency timeouts. Amazon's counterpoint: breakers "introduce modal behavior
  into systems that can be difficult to test"; they prefer a local retry **token bucket**;
  "Retries are 'selfish'"; retry "at a single point in the stack"; jitter periodic work
  deterministically per host so problems repeat in a pattern.
- **Avoiding fallback** (Amazon): fallback paths are "rarely triggered", "hard to test",
  "often have latent bugs", and "often make the outage worse" (the 2001 cache→database
  fallback took down the site and fulfillment). Preferred: make the primary path more
  reliable, "let the caller handle errors" (fail fast), push data proactively, convert
  fallback into continuously exercised failover, and alarm on retry rates so retries do
  not become a hidden fallback.
- **Safe default for a decision loop**: the SRE pipelines rule ("stale data is almost
  always better than incorrect data") plus Amazon's "fail fast" implies the only safe
  partial-failure default is **refuse to act this tick, keep observing, say why** — never
  act on stale or unverified inputs, never silently switch to a degraded model.

---

## 2. Design implications for `dskit.production`

Everything below is a candidate for the ADR, not a decision. File layout is deferred to
the package plan the owner must approve first.

### 2.1 Seams (ABCs with small hooks)

- **`AlertSink` (ABC, tier-1).** `KIND`, `_PARAMS` (default-deny), `__init__(params)` that
  validates loudly and probes reachability at construction (as `mlflow.py` does),
  `@abstractmethod send(alert)` bounded by the sink's own `timeout_s`, `close()`.
  Shipped kinds, all stdlib: `email` (smtplib), `webhook` (urllib JSON POST with a
  payload *template* so one class serves Slack, Discord, PagerDuty Events v2, generic),
  `log` (the structured log), `memory` (tests). A `pkg.mod:Class` reference is honoured
  exactly as `tracking` does.
- **`AlertRouter` (tier-1).** Owns the policy the sinks must not: fingerprint dedup,
  `group_wait_s`, `repeat_interval_s`, per-severity routes, token-bucket rate limit,
  inhibition (a `critical` health alert mutes its `warning` children), silences,
  escalation (`escalate_after_s` un-acked → next route), `ack(key)`, `resolve(key)`. Sink
  calls run on a worker thread fed by a **bounded** `queue.Queue`; `put_nowait` failure
  and every sink exception are swallowed and **counted** (`alert_sink_failures_total`,
  `alerts_suppressed_total{why}`). State lives in `sqlite3` so restarts do not re-page.
- **`HealthProbe` (ABC)** with `name`, `scope ∈ {local, dependency}`, `timeout_s`,
  `@abstractmethod check() -> ProbeResult(ok, detail)`; probes run on a background thread
  updating a flag, and the loop alarms if that thread dies (Amazon).
- **`Health` (state machine).** `STARTING → READY | DEGRADED | UNHEALTHY → STOPPING`.
  Inputs: probe results, `age of last successful tick`, `data age`. Policy from §1.2:
  local failures → `UNHEALTHY` (stop acting **and stop heartbeating**, so the external
  dead-man pages and a supervisor may restart); dependency failures or staleness →
  `DEGRADED` (keep observing, **refuse acts**, raise an alert); `failure_threshold` /
  `success_threshold` hysteresis. Transitions, not levels, generate alerts.
- **`Heartbeat` emitters (tier-1):** `file` (mtime + JSON status), `systemd`
  (`$NOTIFY_SOCKET` datagram `WATCHDOG=1`, `READY=1`, `STATUS=`), `url` (healthchecks-
  compatible: success, `/start`, `/fail`, `rid=<tick_id>`). Sent only when `Health` is
  `READY`/`DEGRADED`-observing as configured. The package never pages on its own death;
  the external consumer does.
- **`Metrics` registry (tier-1)** — counter / gauge / histogram with Prometheus naming
  rules enforced (base units, `_total`, closed label sets, a cardinality cap that
  refuses new label values beyond `labels_max_cardinality`), flushed as JSONL per tick;
  tier-2 packs (`prometheus_client` exposition/pushgateway, `opentelemetry`) subscribe to
  the same registry.
- **`TickRecord` writer** — exactly one record per tick, written in a `finally` so
  refused and crashed ticks still produce one; a `contextvars` tick id stamps every
  ordinary log line; an opt-in redaction filter (field allow-list + regex deny-list).
- **`AuditLedger`** — hash-chained, insert-only ledger of **declared acts** (two-phase:
  `declared` → `submitted` → `confirmed|rejected|cancelled` → `reconciled`), `verify()`
  walks the chain, and `anchor` mails/posts the head hash every N rows via the router.
- **`Arming` / kill switch.** `mode ∈ {paper, live}`; `live` additionally requires an
  `arm_token` from the environment and the absence of `kill_switch_file`; arming emits a
  `critical`-routed `LIVE ARMED` notice with config hash and limits at startup; the
  kill-switch file is polled every tick and, when present, refuses new acts and calls the
  child's `cancel_pending()` hook. Every act record carries `armed: true`, the
  `config_hash`, `tick_id`, and a client-generated **idempotency key** (= `act_id`).
- **`Readiness` checklist evaluator** — a JSON checklist (question, required, evidence,
  waiver) evaluated to GO / NO-GO; NO-GO exits 3, mirroring the pipeline gate.

### 2.2 Closed severity vocabulary (pinned by a test)

| dskit | PagerDuty | OTel number | syslog | Python logging | default route |
|---|---|---|---|---|---|
| `info` | info | 9 | 6 | 20 | log only |
| `warning` | warning | 13 | 4 | 30 | ticket-style sink (email/webhook), never page |
| `error` | error | 17 | 3 | 40 | ticket-style, rate-limited |
| `critical` | critical | 21 | 2 | 50 | page; bypasses rate limit, still deduped |

Severity is *perceived seriousness*; **routing** (page / ticket / log) is a separate
config dimension. Status is `firing|resolved`. Anything else is refused by name.

### 2.3 Config knobs (types / bounds; defaults from the sources)

```jsonc
"production": {
  "mode": "paper",                       // "paper" | "live"; live needs arming (§2.1)
  "tick":      { "period_s": 60,         // > 0
                 "jitter_s": 3,          // 0 ≤ jitter < period (Amazon: jitter periodic work)
                 "timeout_s": 45,        // 0 < timeout ≤ period
                 "stale_after_s": 120,   // ≥ 2 × period (Prometheus batch rule)
                 "data_stale_after_s": 90 },
  "health":    { "failure_threshold": 3, "success_threshold": 1,     // ints ≥ 1 (k8s defaults)
                 "probe_timeout_s": 1.0,                             // (0, tick.timeout_s]
                 "probes": [ { "kind": "...", "scope": "dependency", "params": {} } ] },
  "heartbeat": { "every_s": 60,          // ≤ period; ≥ 12 s if kind=url (≤ 5 pings/min)
                 "emitters": [ { "kind": "url", "params": { "url": "$env:HC_URL", "timeout_s": 5 } } ] },
  "alerts":    { "sinks":  [ { "kind": "email"|"webhook"|"log"|"pkg.mod:Class", "params": { "timeout_s": 5 } } ],
                 "routes": [ { "severity": "critical", "sinks": ["pager"], "page": true } ],
                 "group_wait_s": 30, "repeat_interval_s": 14400,      // [0,600], [60,86400]
                 "rate_limit": { "max_per_hour": 20, "burst": 5 },     // ints ≥ 1
                 "escalate_after_s": 900, "inhibit": [], "silences": [] },
  "logging":   { "dir": "...", "level": "INFO", "rotate": { "when": "midnight", "backup_count": 14 },
                 "queue_size": 10000, "redact": { "allow_fields": [], "deny_patterns": [] } },
  "audit":     { "path": "...", "hash": "sha256", "anchor_every_n": 1000, "retention_days": 2555 },
  "metrics":   { "sinks": [ { "kind": "jsonl" } ], "histogram_buckets_s": [0.05, 0.25, 1, 5, 30],
                 "labels_max_cardinality": 50 },
  "slo":       { "objectives": [ { "sli": "tick_success"|"freshness"|"act_confirmed", "target": 0.99, "window_days": 30 } ],
                 "burn_rate_alerts": [ { "long_s": 3600, "short_s": 300, "factor": 14.4, "severity": "critical" } ] },
  "act":       { "armed": false, "arm_token_env": "DSKIT_ARM", "kill_switch_file": "...",
                 "max_acts_per_tick": 1, "max_open_exposure": 100.0, "exposure_unit": "USD" },
  "readiness": { "checklist": "configs/readiness.json", "waivers": [] }
}
```

Identity: `act.*`, `mode`, `tick.*`, `slo.*` change what a run *does* and are graded;
`alerts`, `logging`, `heartbeat`, `metrics.sinks`, `audit.path` are placement and join
`env/outputs/schedule/tracking` in the non-identity list. Every `params` block is
default-deny; a default lives in exactly one named constant (`DEFAULT_GROUP_WAIT_S`),
never in two `.get()` calls.

### 2.4 Records to persist

1. **TickRecord** (JSONL + sqlite index): `tick_id` (uuid4), `seq`, `started_at`,
   `ended_at`, `asof`, `config_hash`, `mode`, `armed`, `health_state`, `data_age_s`,
   input summary (counts/hashes, never payloads), decision + *why*, guards evaluated
   (name, value, limit, verdict), refusal reason (closed set), act ids declared, timers,
   counters, error class + category (`client_fault|dependency|internal`), sink outcomes.
2. **ActRecord** (hash-chained ledger): `act_id` (idempotency key), `tick_id`, phase
   timestamps, redacted params, venue acknowledgement ids, fills/confirmations, cancels,
   reconciliation result, `prev_hash`, `hash`.
3. **AlertRecord**: fingerprint, severity, first_seen, last_sent, count, status,
   acked_by/at, per-sink attempt outcomes.
4. **HealthTransition**: from, to, at, cause, probe evidence.
5. **ReconciliationSnapshot**: expected vs observed per key with diffs and the action
   taken (refuse / alert / auto-correct).
6. **ReadinessResult**: item, status (pass/fail/waived), evidence, reviewer, at, verdict.

### 2.5 Tiering

- **Tier-1 (stdlib only)**: `logging` (+`handlers`, `QueueHandler`/`QueueListener`),
  `json`, `sqlite3`, `hashlib`/`hmac`, `uuid`, `smtplib`/`email`, `urllib.request`,
  `socket` (systemd notify, TCP probes), `threading`/`queue`, `signal` (SIGTERM →
  `STOPPING`, finish the tick, reconcile), `contextvars`, `http.server` (optional local
  `/healthz` for a supervisor), `statistics`, `time`/`datetime`.
- **Tier-2 packs** (imported inside methods): `prometheus_client` (exposition + push),
  `opentelemetry` (logs/metrics/traces), optionally `sentry_sdk`. Slack / Discord /
  PagerDuty / healthchecks need **no pack** — they are JSON-over-HTTPS templates.
- **Tier-3 child**: the concrete probes, the guards' thresholds, exposure units, the
  `cancel_pending()` and `reconcile()` hooks, the readiness checklist content.

### 2.6 Tests to write

- `test_sink_failure_never_kills_the_loop` — a sink whose `send` raises; the tick
  completes, `alert_sink_failures_total{sink}` increments, other sinks still deliver.
- `test_a_hanging_sink_is_bounded` — a local `socket` server that accepts and never
  replies; sink `timeout_s=0.5`; tick wall-time < timeout + margin; queue full →
  dropped-and-counted, never blocked.
- `test_sink_refuses_at_construction` — unknown param, missing URL, unreachable host,
  `HTTPHandler`-style no-timeout configuration → `ConfigError` before any tick.
- `test_dedup_group_wait_repeat_interval`, `test_resolve_emits_resolved`,
  `test_rate_limit_token_bucket`, `test_critical_bypasses_rate_limit_not_dedup`,
  `test_inhibition_mutes_children`, `test_silence_window`,
  `test_escalation_after_unacked`, `test_router_state_survives_restart`.
- `test_health_transitions` (stale → DEGRADED; `failure_threshold` → UNHEALTHY;
  `success_threshold` recovers), `test_dependency_failure_refuses_acts_but_stays_alive`,
  `test_probe_thread_death_is_detected`.
- `test_heartbeat_stops_when_unhealthy`, `test_heartbeat_rid_is_tick_id`,
  `test_systemd_notify_datagram_format`.
- `test_one_record_per_tick_including_refused_and_crashed`,
  `test_redaction_is_opt_in`, `test_log_lines_carry_tick_id`,
  `test_logging_failure_does_not_stop_the_loop` (unwritable dir).
- `test_audit_chain_verifies`, `test_audit_tamper_is_located` (edit a middle row →
  break reported at that `seq`), `test_declared_precedes_submitted`,
  `test_idempotency_key_stable_across_retry`, `test_anchor_posts_head_hash`.
- `test_live_requires_arming`, `test_arming_emits_loud_notice`,
  `test_kill_switch_refuses_within_one_tick_and_cancels_pending`.
- `test_severity_vocabulary_is_closed`, `test_severity_map_agrees_everywhere` (pins the
  §2.2 table), `test_metric_names_follow_conventions`, `test_label_cardinality_cap`,
  `test_tick_bounds_agree` (timeout ≤ period, stale_after ≥ 2×period),
  `test_identity_excludes_placement_sections`, `test_readiness_no_go_exits_3`,
  `test_sigterm_finishes_tick_then_stops`, and the **purity gate** extended to
  `dskit/production/*.py`.

---

## 3. Pitfalls and anti-patterns

1. **Paging on causes** ("DB connection refused") instead of symptoms ("no successful
   tick for 2 periods", "act unconfirmed after 3 ticks", "exposure over limit").
2. **A heartbeat that means "process alive" rather than "loop healthy"** — a deadlocked
   or refusing loop keeps the dead-man quiet. Tie the beat to `Health`.
3. **Health checks that turn soft dependencies into hard ones**, and fast automation
   (restart / self-eject) on dependency probes → cascading restarts (k8s, Amazon).
4. **No timeout anywhere** (`HTTPHandler`, default `urlopen`, default `smtplib`); a
   hanging destination stalls the tick. Bound every remote call and the sink queue.
5. **Rate windows on low-traffic loops** — one bad tick out of ten is a 10% error rate;
   use count windows or SLOs sized to real impact.
6. **Unbounded label cardinality** (tick ids, order ids, symbols as labels) and free-text
   refusal reasons; both explode series and hide trends. Reasons are a closed set.
7. **Silent fallback** — acting on cached/stale data, swapping to a "simpler model" on
   error, retrying money-moving calls without idempotency keys. Refuse and say why.
8. **Alert storms and un-deduplicated repeats**: no `group_wait`, no `repeat_interval`, no
   inhibition; every tick re-pages the same fault. Also the inverse: dedup keys that
   never resolve, so a recurrence after a fix is swallowed.
9. **Secrets and payloads in logs** (webhook URLs are credentials; venue payloads are
   PII). Opt-in fields, central redaction, encrypted/rotated storage.
10. **Multiple log lines per tick with no shared id**; trace ids reconstructed by
    timestamp guessing. One record per tick, id stamped everywhere.
11. **Audit log the writer can rewrite** (same role can `UPDATE`), no chain, no external
    anchor, unsynchronized clocks.
12. **Duplicated defaults** — a threshold in `validate_params` and a different one in
    `run()`; severity tables restated in three modules with nothing pinning them.
13. **Runbook-less alerts** and runbooks that are deterministic scripts (automate those).
14. **Launching without a readiness pass**: no rollback, no kill switch rehearsal
    (game day), no failure-model table, limits nobody can justify.
15. **Arming by config alone** — a stray `mode: live` in a copied JSON moves money. Require
    the second factor and the loud notice.
16. **Metrics without purpose** kept "just in case"; the SRE rule is that signals not on a
    dashboard or in an alert are removal candidates.

---

## 4. Sources

Google SRE
- Monitoring Distributed Systems — https://sre.google/sre-book/monitoring-distributed-systems/
- Practical Alerting (Borgmon) — https://sre.google/sre-book/practical-alerting/
- Being On-Call — https://sre.google/sre-book/being-on-call/
- Evolving SRE Engagement Model (PRR) — https://sre.google/sre-book/evolving-sre-engagement-model/
- Workbook: Alerting on SLOs — https://sre.google/workbook/alerting-on-slos/
- Workbook: Implementing SLOs — https://sre.google/workbook/implementing-slos/
- Workbook: Monitoring — https://sre.google/workbook/monitoring/
- Workbook: On-Call — https://sre.google/workbook/on-call/
- Workbook: Data Processing Pipelines — https://sre.google/workbook/data-processing/
- Rob Ewaschuk, My Philosophy on Alerting — https://docs.google.com/document/d/199PqyG3UsyXlwieHaqbGiWVa8eMWi8zzAn0YfcApr8Q/edit (mirror: https://linuxczar.net/sysadmin/philosophy-on-alerting/)

Prometheus / Alertmanager
- Alerting practices — https://prometheus.io/docs/practices/alerting/
- Metric and label naming — https://prometheus.io/docs/practices/naming/
- When to use the Pushgateway — https://prometheus.io/docs/practices/pushing/
- Alertmanager concepts — https://prometheus.io/docs/alerting/latest/alertmanager/
- Alertmanager configuration (group_wait, repeat_interval, inhibit_rule, webhook) — https://prometheus.io/docs/alerting/latest/configuration/
- prometheus_client — https://prometheus.github.io/client_python/ ; pushgateway — https://prometheus.github.io/client_python/exporting/pushgateway/
- Watchdog / dead man's snitch pattern — https://runbooks.gitlab.com/monitoring/prometheus-snitch/ ; https://training.promlabs.com/training/monitoring-and-debugging-prometheus/metrics-based-meta-monitoring/end-to-end-watchdog-alerts/

Health, heartbeats, watchdogs
- Kubernetes probes — https://kubernetes.io/docs/concepts/workloads/pods/probes/
- Kubernetes controllers (reconciliation) — https://kubernetes.io/docs/concepts/architecture/controller/
- Amazon Builders' Library, Implementing health checks — https://aws.amazon.com/builders-library/implementing-health-checks/ (PDF: https://d1.awsstatic.com/builderslibrary/pdfs/implementing-health-checks.pdf)
- healthchecks.io: configuring checks — https://healthchecks.io/docs/configuring_checks/ ; docs — https://healthchecks.io/docs/ ; pinging API — https://healthchecks.io/docs/http_api/
- systemd WatchdogSec — https://man7.org/linux/man-pages/man5/systemd.service.5.html ; sd_notify — https://man7.org/linux/man-pages/man3/sd_notify.3.html
- Azure Well-Architected, Health modeling — https://learn.microsoft.com/en-us/azure/well-architected/cross-cutting-guides/health-modeling
- Azure Architecture Center, Circuit Breaker — https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker

Logging, audit, tracing
- Amazon Builders' Library, Instrumenting distributed systems for operational visibility — https://aws.amazon.com/builders-library/instrumenting-distributed-systems-for-operational-visibility/ (PDF: https://d1.awsstatic.com/builderslibrary/pdfs/instrumenting-distributed-systems-for-operational-visibility.pdf)
- OWASP Logging Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- OpenTelemetry Logs Data Model — https://opentelemetry.io/docs/specs/otel/logs/data-model/
- W3C Trace Context — https://www.w3.org/TR/trace-context/
- Python logging cookbook, "Dealing with handlers that block" — https://docs.python.org/3/howto/logging-cookbook.html ; logging.handlers — https://docs.python.org/3/library/logging.handlers.html ; smtplib — https://docs.python.org/3/library/smtplib.html
- Hash-chained audit trails — https://appmaster.io/blog/tamper-evident-audit-trails-postgresql ; https://www.designgurus.io/answers/detail/how-do-you-design-tamperevident-audit-logs-merkle-trees-hashing
- NIST SP 800-92 (log management) — https://csrc.nist.gov/pubs/sp/800/92/final ; rev. 1 draft — https://csrc.nist.gov/pubs/sp/800/92/r1/ipd
- Stripe idempotent requests — https://docs.stripe.com/api/idempotent_requests
- RFC 5424 severities — https://datatracker.ietf.org/doc/html/rfc5424

Alert sinks
- PagerDuty Events API v2 — https://developer.pagerduty.com/docs/events-api-v2-overview ; event management / dedup_key — https://support.pagerduty.com/main/docs/event-management
- Slack incoming webhooks — https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks
- Discord rate limits — https://discord.com/developers/docs/topics/rate-limits
- Alert fatigue statistics (SOC surveys, indicative) — https://medium.com/anton-on-security/antons-alert-fatigue-the-study-0ac0e6f5621c ; https://www.vectra.ai/topics/alert-fatigue

Readiness reviews, runbooks, regulation
- AWS Operational Readiness Reviews whitepaper — https://docs.aws.amazon.com/wellarchitected/latest/operational-readiness-reviews/wa-operational-readiness-reviews.html ; Appendix B example questions — https://docs.aws.amazon.com/wellarchitected/latest/operational-readiness-reviews/appendix-b-example-orr-questions.html ; OPS07-BP02 — https://docs.aws.amazon.com/wellarchitected/latest/framework/ops_ready_to_support_const_orr.html
- Runbook anatomy — https://openobserve.ai/blog/on-call-runbook-template-sre/ ; https://rootly.com/incident-response/runbooks
- MiFID II RTS 6 Art. 12 "Kill functionality" — https://www.handbook.fca.org.uk/techstandards/MIFID-MIFIR/2017/reg_del_2017_589_oj/chapter-ii/section-3/016.html ; https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng
- SEC Rule 15c3-5 FAQ — https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0

Degradation, flags, retries
- Martin Fowler, Feature Toggles — https://martinfowler.com/articles/feature-toggles.html
- Amazon Builders' Library, Avoiding fallback in distributed systems — https://aws.amazon.com/builders-library/avoiding-fallback-in-distributed-systems/ (PDF: https://d1.awsstatic.com/builderslibrary/pdfs/avoiding-fallback-in-distributed-systems.pdf)
- Amazon Builders' Library, Timeouts, retries, and backoff with jitter — https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/ (PDF: https://d1.awsstatic.com/builderslibrary/pdfs/timeouts-retries-and-backoff-with-jitter.pdf)
- Unleash, kill switches / graceful degradation — https://www.getunleash.io/feature-flag-use-cases-software-kill-switches
