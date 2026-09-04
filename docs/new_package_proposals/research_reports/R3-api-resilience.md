# R3 — Resilient integration with external APIs from a production decision system

*Research memo for the design of `dskit.production` (working name). 2026-09-04.
Method: primary sources on the web (cited in §4) plus a read-only look at the
repo's existing seams. Domain-neutral throughout: venues appear only as
evidence of a convention, never as a design input.*

**Repo precedents consulted (read-only):** `dskit/onboarding/connector.py`
(`MAX_BACKOFF_S = 60.0`, the four-verb `Connector` ABC, default-deny `spec()`),
`dskit/onboarding/libs/predexon.py` (`RateLimiter(min_interval_s, clock,
sleeper)`; a clock that steps backwards "reads as no time elapsed, never as
credit"; `getter(url, params, headers, timeout) -> (status, headers, body)` as
the one transport seam), `dskit/onboarding/libs/kalshi.py` (`_backoff(attempt)
= min(base * 2**attempt, MAX_BACKOFF_S)`, numeric `Retry-After` capped),
`dskit/onboarding/libs/alpaca_quotes.py` (`_Pacer`, a monotonic token gap),
`dskit/onboarding/oauth.py` (env-var NAMES not values; `_REFRESH_MARGIN_SECONDS
= 60`; owner-only 0600 token files).

---

## 1. Findings by theme

### 1.1 Retry design

**Backoff shape.** The AWS Architecture Blog compares three jittered schedules:
Full Jitter `sleep = random(0, min(cap, base * 2**attempt))`, Equal Jitter
`base*2**attempt/2 + random(0, base*2**attempt/2)`, Decorrelated
`min(cap, random(base, sleep*3))`. Full and Equal jitter do about the same total
work; Decorrelated needs more calls; Full Jitter finishes fastest. Capped
exponential backoff *without* jitter still clusters retries [1].

**A mature, fully specified retry policy** (AWS SDK 2026 behaviour) is worth
copying almost verbatim because every knob is named and bounded [2]:
- `max_attempts` 3 (initial + 2 retries); `1` disables retries.
- Three error classes, matched on error *code* first, then HTTP status:
  *transient* (request timeout, connection reset, DNS failure, socket timeout,
  any 500/502/503/504 without a recognised code) with base delay 50 ms;
  *throttling* (429-class codes; a 5xx carrying a throttling code counts as
  throttling) with base delay 1 000 ms; *non-retryable* (validation, access
  denied, not found) returned immediately.
- `delay = random(0,1) × min(20 000 ms, base × 2^retry)` — full jitter, cap 20 s.
- Server-directed timing (`x-amz-retry-after`) is *clamped* to
  `[computed, computed + 5 s]` and not jittered — the server never dictates an
  unbounded wait.
- **Retry quota (token bucket):** capacity 500; a transient retry costs 14, a
  throttling retry 5; a successful retry refunds its own cost; a first-try
  success refunds 1. With 3 attempts the bucket starts draining above ~22 %
  sustained transient failure (~32 % for throttling). Scope: one client
  instance. The initial request is never delayed by the quota.
- *Adaptive* mode adds a client-side rate limiter that can delay the *initial*
  request; it is per-client, so throttling on one resource slows all — "not
  recommended as a general default".

**Retry budgets elsewhere.** Google SRE: per-request budget of 3 attempts;
per-client budget — retry only while retries are < 10 % of requests; backends
can answer "overloaded; don't retry"; retry at *one* layer only ("a failed
request from the DB Frontend should only be retried by Backend B, the layer
immediately above it") — three layers × 3 attempts is 64 attempts per user
action [3][4]. Also: "Always use randomized exponential backoff", a process-wide
budget such as "60 retries per minute", and never retry "permanent errors or
malformed requests" [4]. Envoy replaces a fixed `max_retries` (default 3) with a
budget: `budget_percent` 20 % of active + pending requests, floor
`min_retry_concurrency` 3 [5]. Client-side adaptive throttling (SRE): reject
locally with probability `(requests − K·accepts)/(requests+1)`, K = 2 [3].

**Timeouts are mandatory and come from percentiles.** Pick "an acceptable
rate of false timeout (e.g. 0.1%)" and use that percentile of downstream
latency; "HTTP client errors (4XX errors) are not worth retrying at all";
add jitter "to all timers, periodic jobs or delayed work" [6]. `requests` has
*no* default timeout — "your code may hang for minutes or more"; the tuple
`(connect, read)` (e.g. `(3.05, 27)`) sets them separately, and the connect
timeout applies *per address*, so a dual-stack host doubles it [7]. `httpx`
defaults to 5 s and distinguishes connect/read/write/pool timeouts, "careful to
enforce timeouts everywhere by default" [8]. Google: set deadlines and
propagate the remaining budget downstream [4].

**What is retryable, per the standard.** RFC 9110: GET, HEAD, PUT, DELETE,
OPTIONS, TRACE are idempotent; "a client SHOULD NOT automatically retry a
request with a non-idempotent method unless it has some means to know that the
request semantics are actually idempotent or … that the outcome of the original
request will not be duplicated"; `Retry-After` is delay-seconds *or* an
HTTP-date [9]. `urllib3.Retry` encodes exactly this: `allowed_methods`
defaults to the idempotent set; `RETRY_AFTER_STATUS_CODES = {413, 429, 503}`;
`respect_retry_after_header=True`; but `backoff_max` 120 s and
`retry_after_max` **21 600 s (6 h)** — a hostile server can park an
un-capped client for hours, which is precisely what `MAX_BACKOFF_S` exists to
refuse [10].

**The unknown-outcome write.** Stripe: after a network error "clients are
usually left in a state where they don't know whether or not the server
received the request… they should retry such requests with the same
idempotency keys and the same parameters until they're able to receive a
result"; "treat the result of a 500 request as indeterminate"; the
`Stripe-Should-Retry: true|false|absent` header lets the server say when a
status code alone is not enough, and `Idempotent-Replayed: true` marks a
replay [11]. AWS describes the same dilemma ("It's not clear whether the
singleton workload is running or not. Simply retrying the request could result
in multiple workloads") and resolves it with a client request identifier plus
stored parameters, returning "a validation error indicating a parameter
mismatch" if the same token arrives with different parameters [12]. A broker's
FAQ takes the conservative line for orders: after a timeout "should not attempt
to resend the order or mark the timed-out order as canceled until confirmed"
[13]. Its SDK adds the rule that matters most: "Do not treat a lookup miss as
proof that the first request was not accepted, and do not assume the order will
eventually become visible" [14].

### 1.2 Idempotency keys, client order IDs, reconciliation

- **IETF `Idempotency-Key` draft (-07):** a String Item Structured Header;
  "MUST be unique and MUST NOT be reused with another request with a different
  request payload"; UUID recommended; the server MAY keep a *fingerprint* of
  the payload; same key + different payload → **422**; same key while the first
  is still in flight → **409**; the resource "SHOULD respond with the result of
  the previously completed operation, success or an error"; retention policy
  "SHOULD… be published" [15]. It has not become an RFC — it is a convention
  Stripe popularised.
- **Stripe:** keys ≤ 255 chars, V4 UUID suggested, "Avoid using sensitive data
  (for example, email addresses…) as idempotency keys"; the first status+body
  is stored "regardless of whether it succeeds or fails" and replayed;
  parameter mismatch errors; results are saved only once execution begins, so
  429/401/most 400s are *not* cached; keys pruned after ≥ 24 h; GET/DELETE
  ignore the header [16][11]. Two key strategies: random, or *derived from a
  user-attached object* (cart id) — the latter protects against double submits
  across process restarts [11].
- **Venue conventions for client order ids:** FIX `ClOrdID` (tag 11) must be
  unique within a trading day, "embedding a date" for multi-day uniqueness; a
  cancel/replace carries a *new* `ClOrdID` plus `OrigClOrdID` [17]. Alpaca:
  `client_order_id` ≤ 128 chars, auto-generated if omitted, retrievable via
  `GET /v2/orders:by_client_order_id` [18]. Kalshi: "Reusing a `client_order_id`
  for a second call returns the original order rather than placing a new one"
  (SDK docs) [19]. Betfair: `customerRef` ≤ 32 chars "used to de-dupe mistaken
  re-submissions" inside a **60-second window**, duplicate → error [20].
  So dedupe semantics differ by venue in *three* ways: replay vs reject vs
  window-limited — and some venues offer none.

**Reconciliation protocol that follows from the above:** (1) generate the key
and persist the intent *before* the first send; (2) send; (3) on `ok`/`fatal`
settle; (4) on `ambiguous` (timeout, connection reset, 5xx, 409 in-flight)
**query by client id**; found → adopt the venue's record; not found → do not
infer absence; apply the venue-declared policy — resend with the *same* key
only where the venue replays or rejects duplicates, otherwise halt and surface
to a human.

### 1.3 Circuit breakers, bulkheads, fallbacks

- **Semantics.** Fowler: closed → (failures reach threshold) open → (timeout)
  half-open trial call → closed or open again; "Any change in breaker state
  should be logged"; "Operations staff should be able to trip or reset
  breakers"; "Not all errors should trip the circuit" [21]. Nygard's stability
  patterns add Timeouts, Bulkheads, Fail Fast, Steady State, Shed Load, Create
  Back Pressure and — most relevant to an automated decision system — the
  **Governor**: "slow automations down enough for humans to get involved and
  prevent catastrophe" [22][23].
- **resilience4j knob set (defaults):** `failureRateThreshold` 50 %,
  `slowCallRateThreshold` 100 %, `slowCallDurationThreshold` 60 s,
  `permittedNumberOfCallsInHalfOpenState` 10, `maxWaitDurationInHalfOpenState`
  0, `slidingWindowType` COUNT_BASED|TIME_BASED, `slidingWindowSize` 100,
  `minimumNumberOfCalls` 100, `waitDurationInOpenState` 60 s,
  `automaticTransitionFromOpenToHalfOpenEnabled` false,
  `recordExceptions`/`ignoreExceptions`/predicates. States CLOSED, OPEN,
  HALF_OPEN plus METRICS_ONLY, DISABLED, FORCED_OPEN; OPEN rejects with
  `CallNotPermittedException` [24].
- **Polly v8:** `FailureRatio` 0.1, `MinimumThroughput` 100, `SamplingDuration`
  30 s, `BreakDuration` 5 s (or a generator), `ShouldHandle`, `ManualControl`
  (Isolate/Close), `StateProvider`, `OnOpened/OnClosed/OnHalfOpened`. Two
  documented traps: "If the MinimumThroughput is not reached during the
  SamplingDuration then the FailureRatio is ignored" (a low-rate order flow
  never trips a breaker tuned for web traffic), and guard expressions that skip
  the call when open prevent the half-open probe from ever running [25].
- **Bulkheads / concurrency.** resilience4j `SemaphoreBulkhead`
  `maxConcurrentCalls` 25, `maxWaitDuration` 0 [26]; Envoy `max_connections`,
  `max_pending_requests`, `max_requests` 1024, `max_retries` 3 [5]. Netflix
  derives the limit from Little's law (`Limit = RPS × latency`) and adapts it
  like a TCP congestion window (Vegas, Gradient2), partitioning capacity (e.g.
  live 90 % / batch 10 %) [27]. Stripe runs four limiters: request rate,
  concurrent requests, fleet-usage shedder, worker-utilisation shedder [28].

### 1.4 Client-side rate limiting

- **Token bucket** is the near-universal primitive ("take tokens on each
  request, and slowly drip more tokens into the bucket. If the bucket is empty,
  reject") [28]; resilience4j's `RateLimiter` is `limitForPeriod` 50 per
  `limitRefreshPeriod`, with `timeoutDuration` 5 s before `RequestNotPermitted`
  [29].
- **Server headers.** IETF draft: `RateLimit-Policy: "burst";q=100;w=60` and
  `RateLimit: "default";r=50;t=30` (r = remaining, t = seconds). Clients "MUST
  NOT assume that a positive available quota is a guarantee"; `Retry-After`
  "MUST take precedence"; and — the security consideration that justifies a
  cap — "A client is responsible for ensuring that RateLimit header field
  values returned cause reasonable client behavior", because a malicious server
  can return huge windows [30]. Legacy `X-RateLimit-*` remains the deployed
  reality. GitHub's documented client algorithm: honour `retry-after`; if
  `x-ratelimit-remaining` is 0 wait until `x-ratelimit-reset`; otherwise wait
  ≥ 1 min then back off exponentially with a max; make requests serially;
  conditional requests returning 304 are free; keep hammering and "your
  integration" may be banned [31].
- **Separate read and write budgets** are a venue fact: one exchange
  publishes per-tier buckets such as 200 reads/s and 100 writes/s, defines
  writes as "order placement, amends, cancels, …", answers 429 and states that
  "429 responses do not currently include `Retry-After` or `X-RateLimit-*`
  headers" [32] — the client must own its budget; header-following alone is not
  a strategy.

### 1.5 Auth and secrets

- **Signed requests with a clock window.** Binance: HMAC-SHA256 over
  `query string ‖ body`; `timestamp` in ms/µs; `recvWindow` default 5 000 ms,
  max 60 000; accepted iff `timestamp < serverTime + 1 s && serverTime −
  timestamp ≤ recvWindow`; RSA and Ed25519 variants; a server-time endpoint for
  skew [33]. Coinbase Exchange: HMAC-SHA256 over `timestamp + method +
  requestPath + body`, integer-second timestamp "within 30 seconds of the API
  service time" [34]. Kalshi: RSA-PSS/SHA-256 over `timestamp(ms) + method +
  path` (no query string), three headers [35]. AWS SigV4: key derived per
  service/region/day; "a request must reach AWS within five minutes of the time
  stamp" [36]. The pattern: `sign(timestamp, method, path[, query][, body])`,
  a skew window of 5 s–5 min, and a server-time probe.
- **OAuth refresh.** RFC 6749 §6: the server "MAY issue a new refresh token,
  in which case the client MUST discard the old refresh token"; `invalid_grant`
  means re-authorise [37]. RFC 9700 (BCP): refresh tokens for public clients
  MUST be sender-constrained or rotated; reuse of an invalidated token reveals a
  breach and revokes the family [38].
- **Key hygiene and rotation.** Stripe: environment is *in the key* —
  `sk_test_`/`sk_live_`, and "objects in one mode aren't accessible to the
  other"; rotation with a 7-day grace period, gradual roll-out, expire only after
  request volume is zero; restricted keys with minimal permissions; access
  policies by IP/ASN; vault first, env var second, never source control [39].
  OWASP: rotate "so that any stolen credentials will only work for a short
  time"; automate; dual-key overlap ("new keys for Write… old keys for Read");
  audit who requested/used a secret; secrets "Never be logged" — mask [40].
- **Logging.** OWASP's exclude list: access tokens, passwords, session ids,
  connection strings, encryption keys, bank/card data, sensitive PII, source
  code; include UTC timestamp, an interaction/correlation identifier, who/what/
  where/when, and result status [41]. Vendors supply a per-call correlation id
  (`X-Request-ID`) [42].

### 1.6 Streaming vs polling

- **Liveness.** RFC 6455: on Ping an endpoint "MUST send a Pong frame in
  response, unless it already received a Close frame"; an unsolicited Pong is a
  "unidirectional heartbeat"; close codes 1000/1001/1006/1011 [43]. The
  `websockets` library pings every 20 s and expects a Pong within 20 s, else
  `ConnectionClosed`; `max_size` 1 MiB, `max_queue` 16 frames; backpressure by
  ceasing to read from the socket above a high-water mark — "it's still
  possible for an application to create its own unbounded buffers and break the
  backpressure. Be careful with queues" [44][45][46].
- **Sequence numbers and snapshot + delta.** Coinbase Exchange: sequence
  numbers are "increasing integer values for each product, with each new
  message being exactly one sequence number greater than the one before it";
  a gap means drops → resync; a lower number is out-of-order → ignore; the
  heartbeat channel (1 Hz) carries `sequence` and `last_trade_id` to "verify
  that no messages were missed"; level2 sends one `snapshot` then `l2update`
  with *absolute* sizes (0 removes the level) [47][48]. Advanced Trade: idle
  channels close after 60–90 s unless `heartbeats` is subscribed [49]. Kalshi:
  server ping every 10 s; `orderbook_snapshot` then `orderbook_delta` with
  `seq`; on a gap "stop acting on the local book, reconnect, and wait for a
  fresh snapshot" [50] — and the failure mode when you don't is a public bug:
  "orderbook permanently desyncs after a sequence gap (no resnapshot)" [51].
- **Polling.** Prefer push where offered; use conditional requests; serialise
  requests; back off on secondary limits [31]. Market data may be *conflated*
  (keep latest per key) under backpressure; order/fill events never may.

### 1.7 Testing without a network

- **Injection** is the repo's established answer (`getter`, `clock`,
  `sleeper`), and the deterministic-simulation literature extends it by one
  item: **the RNG**. FoundationDB/TigerBeetle-style DST abstracts "network,
  disk, time, and random number generation" behind a seeded PRNG so any run
  replays from a seed [52][53]. Jittered backoff is random; an un-injected
  `random` makes retry tests flaky or untestable.
- **Cassettes.** VCR.py record modes: `once` (record if missing, else replay and
  *error on new requests*), `new_episodes` (silently records new ones —
  dangerous in CI), `none` (replay only; anything new errors), `all` (always
  re-record); scrub secrets *before* writing with `filter_headers`,
  `filter_query_parameters`, `filter_post_data_parameters`,
  `before_record_request/response`; `allow_playback_repeats` [54][55].
- **Fault vocabulary.** Toxiproxy's toxics are a ready-made, well-understood
  fault list: `latency`(+`jitter`), `bandwidth`, `slow_close`, `timeout`
  (0 = drop data without closing), `reset_peer`, `slicer`, `limit_data`,
  `packet_loss`; a `toxicity` probability; `upstream` vs `downstream` [56]. An
  in-process fake transport can implement the same names.
- **Contract tests.** Pact's consumer-driven contracts verify a provider you
  run yourself [57]; for third-party venues you cannot, so the contract is a
  *sandbox conformance* job (scheduled, network-allowed) that asserts schema
  shapes and dedupe semantics against the vendor sandbox, kept out of unit CI.
- **Sandbox realism is limited by design:** one paper venue fills only when
  marketable, gives random partial fills 10 % of the time, and simulates no
  slippage, queue position, fees or dividends [58] — so a paper run validates
  plumbing, not execution assumptions.

### 1.8 Sandbox / paper endpoints — the common pattern

| Vendor | Separation mechanism | Fake money? |
|---|---|---|
| Alpaca | different base URL (`paper-api…`) **and** different API keys; same API spec; env var selects base URL [58] | yes |
| Kalshi | distinct demo host, same endpoints, same key-generation process [35] | yes |
| Betfair | **no sandbox** — the "Delayed" app key runs on the live production exchange with 1–180 s delayed prices; the live key ships inactive [20][59] | **no** |
| Interactive Brokers | same platform; live vs paper by *socket port* (TWS 7496/7497, Gateway 4001/4002); API "Read Only" on by default [60][61] | yes |
| Stripe | mode encoded in the key prefix (`sk_test_`/`sk_live_`); objects never cross modes [39] | yes |

The generalisation: an *environment* is the tuple (endpoint, credential set,
capability flags), the tuple must be internally consistent, and "test" does
not imply "no money".

---

## 2. Design implications for `dskit.production`

Constraints honoured: tier-1 = stdlib (`urllib`, `hmac`, `hashlib`, `json`,
`sqlite3`, `threading`, `random`, `time`); behaviour from JSON with default-deny
params; every wait ≤ `MAX_BACKOFF_S`; an *executor object* is passed in; live
is a declared, loud act.

### 2.1 `Retry` — a pure policy object
- **Closed vocabularies.** Outcome kinds: `ok | transient | throttled | fatal |
  ambiguous`. Decisions: `retry | give_up | reconcile`. A write whose outcome
  is `ambiguous` can never yield `retry` — only `reconcile` (§1.1/1.2).
- **Hooks.** `classify(outcome) -> kind` (abstract on a small `Classifier`
  ABC; a default HTTP classifier implements §1.1's table: codes first, status
  second; 408/429/5xx/connection faults; 4xx otherwise fatal);
  `delay(attempt, kind, retry_after)`; `permit(kind)` (budget);
  `decide(attempt, kind, is_write)`.
- **Knobs** (`_PARAMS`): `max_attempts` int [1, 10] = 3; `base_s` float
  (0, `MAX_BACKOFF_S`] = 0.05; `throttle_base_s` = 1.0; `cap_s` ≤
  `MAX_BACKOFF_S` = 20; `jitter` ∈ {`full`, `equal`, `none`} = `full`;
  `retry_after` ∈ {`honor`, `ignore`} = `honor` (always capped);
  `retry_writes` ∈ {`never`, `idempotent_only`} = `idempotent_only` (a write
  is retryable only when an idempotency key travels with it); `budget`
  {`capacity` 500, `transient_cost` 14, `throttle_cost` 5, `refund` 1}.
- **Injected:** `rng` (`random.Random(seed)`), `clock`, `sleeper`.
- **Records:** per attempt `{correlation_id, attempt, kind, delay_s,
  retry_after_seen, budget_left}`.

### 2.2 `CircuitBreaker`
- **States:** `CLOSED | OPEN | HALF_OPEN | FORCED_OPEN | METRICS_ONLY`
  (resilience4j's `DISABLED` folds into `METRICS_ONLY`). `FORCED_OPEN` via
  `trip(reason)` is the software kill switch; `reset()` is the ops verb
  (Fowler). Every transition is a record `{ts, from, to, reason, window
  counts}`.
- **Hooks:** `permit() -> bool` (raises `CircuitOpen` on the executor path),
  `record(kind, elapsed_s)`, `on_transition(old, new, reason)`. Failure
  counting reuses `Retry.classify` — `fatal` business rejections (insufficient
  funds, invalid symbol) are *ignored*, not counted (Fowler; Polly
  `ShouldHandle`).
- **Knobs:** `window` {`kind` ∈ {`count`, `time`}, `size` int ≥ 1} =
  count/20; `min_calls` int ≥ 1 = 5 (not 100 — order flow is low-rate, §1.3);
  `failure_rate` (0, 1] = 0.5; `slow_call_s` = the transport timeout;
  `slow_rate` (0, 1] = 1.0; `open_s` (0, 600] = 30; `half_open_calls` ≥ 1 = 1;
  `auto_half_open` bool = true. One breaker **per scope** (reads vs writes vs
  stream), never one for the venue.
- **Injected:** `clock`.

### 2.3 `RateLimiter` — token buckets per scope, plus a bulkhead
- **Knobs:** per `scope` (`reads`, `writes`, `stream_subscribe`, …):
  `rate_per_s` > 0, `burst` ≥ 1, `max_wait_s` [0, `MAX_BACKOFF_S`] (0 = fail
  fast with `RateLimited`), `max_in_flight` ≥ 1 (the bulkhead; default 1 for
  writes — serial by default, per GitHub's advice), `honor_headers` bool = true.
- **Hooks:** `acquire(scope, cost=1)`, `release(scope)`,
  `observe(headers)` — parses `Retry-After`, `RateLimit`, `X-RateLimit-*`;
  every derived pause is capped (IETF security consideration); a venue with no
  headers still runs on the configured bucket.
- **Injected:** monotonic `clock`, `sleeper`. Backwards clock steps read as
  zero elapsed (repo precedent).
- **Records:** `{scope, waited_s, rejected, source ∈ {bucket, header}}`.

### 2.4 `Idempotency` — a write-ahead intent ledger (sqlite3)
- **State vocabulary:** `pending → sent → acked | rejected | ambiguous`;
  `ambiguous → acked | rejected | orphaned` (orphaned = lookup miss after the
  venue's window; needs a human).
- **Hooks:** `begin(intent) -> key` (persists `{key, fingerprint(sha256 of
  canonical intent), created_at, state}` *before* any send);
  `sent(key)`, `settle(key, result)`, `mark_ambiguous(key)`,
  `reconcile(key)` which calls the executor's `find_by_client_id(key)`.
  A key reused with a different fingerprint is refused locally (mirrors the
  draft's 422).
- **Knobs:** `key_format` ∈ {`uuid4`, `derived`} = `uuid4`;
  `key_max_len` (venue fact, e.g. 32/128/255); `dedupe` ∈ {`replays`,
  `rejects`, `window`, `none`} with `dedupe_window_s`; `retention_s`;
  `on_ambiguous` ∈ {`reconcile_then_halt`, `reconcile_then_resend`} =
  `reconcile_then_halt`; `resend` is only *permitted* when `dedupe` ∈
  {`replays`, `rejects`}, refused at config-check time otherwise. Keys carry no
  PII (Stripe).
- **Records:** the ledger *is* the audit record; export as JSON lines.

### 2.5 `Environment` and the live switch — the loud act
- Config declares `environment: {"kind": "paper"|"live", "base_url",
  "credentials": {…env var NAMES…}}`. **Live requires conjunction:**
  `kind == "live"` in the config, a CLI flag `--live`, an arming env var whose
  value equals the config's identity hash (so a copied config cannot arm a
  different run), and the executor's own `describe() -> {"kind", "endpoint",
  "credential_fingerprint"}` agreeing with the config. Any disagreement
  refuses; anything absent runs `paper` or `dry_run`. Arming emits a banner and
  an `armed` record carrying the identity hash.
- **Governor knobs** (Nygard; SEC 15c3-5's "pre-set credit or capital
  thresholds" as precedent [62]): `max_writes_per_minute`, `max_order_qty`,
  `max_notional`, `max_open_orders`, `max_daily_loss`, `confirm_first_n_writes`.
  Mechanism in dskit; numbers in the child's config.

### 2.6 Seams and tier-2 packs
- **`Transport`** ABC: `send(method, url, headers, body, timeout) -> (status,
  headers, body)` — default `urllib`; `requests`/`httpx` packs import inside
  the method. `timeout` is `{connect_s, read_s}`, both required; `None`
  refused (§1.1).
- **`Signer`** ABC: `sign(method, path, query, body, timestamp) -> headers`.
  HMAC-SHA256 is stdlib (tier-1); RSA-PSS/Ed25519 need `cryptography` → tier-2
  pack. Knobs: `skew_window_s`, `time_probe` (a GET whose response gives server
  time); refuse to sign when measured skew exceeds the window.
- **`Secrets`**: the existing façade — env-var names only, redacting `repr`,
  refuses JSON — plus `redact(text)` applied to every log line and cassette.
- **`Stream`** ABC: `connect / subscribe / next / close`; knobs
  `heartbeat_s`, `stale_after_s`, `seq_field`, `on_gap` ∈ {`resnapshot`,
  `reconnect`, `halt`}, `queue` {`max`, `overflow` ∈ {`conflate`, `block`,
  `halt`}}; the gap detector and snapshot+delta applier are pure tier-1 code;
  the `websockets` pack only moves bytes.
- **`FakeVenue`** (tier-1, tests): scripted transport with the Toxiproxy fault
  names, `toxicity`, a seeded `rng`, a virtual clock; JSON cassettes with
  `record_mode` ∈ {`none`, `once`, `all`} and redaction at record time.

### 2.7 Injected for tests
`clock` (monotonic and wall), `sleeper`, `rng`, `transport`, `secrets`
resolver, ledger path. Production defaults for all; a test passes fakes and
runs a thousand-attempt retry storm in milliseconds.

### 2.8 Records to persist
Attempt log; breaker transitions; limiter waits; the idempotency ledger; arm/
disarm events with identity hash; correlation ids (our key + vendor request
id); redacted request summaries `{method, path, status, elapsed_s, bytes}`;
bodies only under an explicit `log_bodies` knob and always through `redact`.

---

## 3. Pitfalls and anti-patterns

- Blind retry of a timed-out POST (RFC 9110); retrying 4xx; retry without
  jitter; retries at several layers (4³ = 64 attempts); honouring an un-capped
  `Retry-After` (urllib3's default permits 6 h); no timeout at all (`requests`).
- A breaker that counts business rejections as outages; `min_calls` sized for
  web traffic so a low-rate order path never trips; a guard that skips the call
  when open so the breaker never half-opens; one breaker for the whole venue.
- Idempotency: reusing a key with a changed payload; PII in keys; generating
  the key *after* the send (a crash in between orphans the order); treating a
  lookup miss as proof of absence; resending on a venue whose dedupe is a
  60-second window that has already passed.
- Environment: assuming "test" means "no money" (Betfair); selecting live by a
  string that a typo can produce; endpoint and credentials from different
  environments; the same credentials serving both (port-only separation).
- Secrets: headers or bodies in logs, cassettes or `repr`; keys in tracebacks.
- Clocks: signing with a skewed clock; pacing on wall-clock time.
- Adaptive limiting shared across scopes (throttling on one resource stalls all).
- Streaming: no heartbeat (silent dead socket); no sequence tracking (a
  permanently desynced book); reconnect without resubscribe/resnapshot;
  conflating order events; unbounded application queues.
- Testing: `new_episodes` in CI quietly touching the network; calibrating
  execution assumptions on a paper venue's synthetic fills.

---

## 4. Sources

1. AWS Architecture Blog — Exponential Backoff And Jitter: https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
2. AWS SDKs and Tools Reference — Retry behavior (2026): https://docs.aws.amazon.com/sdkref/latest/guide/feature-retry-behavior.html
3. Google SRE Book — Handling Overload: https://sre.google/sre-book/handling-overload/
4. Google SRE Book — Addressing Cascading Failures: https://sre.google/sre-book/addressing-cascading-failures/
5. Envoy — circuit_breaker.proto (Thresholds, RetryBudget): https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/cluster/v3/circuit_breaker.proto and https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking
6. Amazon Builders' Library — Timeouts, retries, and backoff with jitter: https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/ (quoted via https://lumigo.io/blog/amazon-builders-library-in-focus-1-timeouts-retries-and-backoff-with-jitter/)
7. Requests — Advanced Usage, Timeouts: https://requests.readthedocs.io/en/latest/user/advanced/#timeouts
8. HTTPX — Timeouts: https://www.python-httpx.org/advanced/timeouts/
9. RFC 9110 — HTTP Semantics (§9.2.2 idempotent methods, §10.2.3 Retry-After): https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods
10. urllib3 — `urllib3.util.Retry`: https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html
11. Stripe — Advanced error handling (network errors, `Stripe-Should-Retry`, idempotency): https://docs.stripe.com/error-low-level
12. Amazon Builders' Library — Making retries safe with idempotent APIs: https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/
13. Alpaca — Working with /orders (timeout FAQ, client_order_id): https://docs.alpaca.markets/us/docs/working-with-orders
14. Alpaca SDK guidance on ambiguous placement (via npm package docs): https://www.npmjs.com/package/@alpacahq/alpaca-trade-api
15. IETF draft-ietf-httpapi-idempotency-key-header-07: https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header-07
16. Stripe — Idempotent requests: https://docs.stripe.com/api/idempotent_requests ; Stripe blog — Designing robust and predictable APIs with idempotency: https://stripe.com/blog/idempotency
17. FIX 4.4 — ClOrdID (tag 11): https://fiximate.fixtrading.org/legacy/en/FIX.4.4/tag11.html ; OrderCancelReplaceRequest: https://www.onixs.biz/fix-dictionary/latest/msgType_G_71.html
18. Alpaca — Get Order by Client Order ID: https://docs.alpaca.markets/reference/getorderbyclientorderid
19. Kalshi client_order_id semantics (SDK docs): https://github.com/TexasCoding/kalshi-python-sdk/blob/main/docs/resources/orders.md
20. Betfair — placeOrders (customerRef dedupe, 60 s window): https://betfair-developer-docs.atlassian.net/wiki/spaces/1smk3cen4v3lu3yomq5qye0ni/pages/2687496/placeOrders ; FAQ: https://developer.betfair.com/en/exchange-api/faq/
21. Martin Fowler — CircuitBreaker: https://martinfowler.com/bliki/CircuitBreaker.html
22. Nygard — Release It! 2nd ed. (stability patterns incl. Governor): https://pragprog.com/titles/mnee2/release-it-second-edition/
23. Notes on Release It! (Governor, Shed Load, Back Pressure): https://john.dev/posts/2019-04-14-release-it-notes.html
24. resilience4j — CircuitBreaker: https://resilience4j.readme.io/docs/circuitbreaker
25. Polly — Circuit breaker resilience strategy: https://www.pollydocs.org/strategies/circuit-breaker.html
26. resilience4j — Bulkhead: https://resilience4j.readme.io/docs/bulkhead ; TimeLimiter: https://resilience4j.readme.io/docs/timeout
27. Netflix — concurrency-limits: https://github.com/Netflix/concurrency-limits
28. Stripe — Scaling your API with rate limiters: https://stripe.com/blog/rate-limiters
29. resilience4j — RateLimiter: https://resilience4j.readme.io/docs/ratelimiter
30. IETF draft-ietf-httpapi-ratelimit-headers: https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-ratelimit-headers
31. GitHub — Best practices for using the REST API: https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
32. Kalshi — Rate limits: https://docs.kalshi.com/getting_started/rate_limits
33. Binance — Request security (HMAC, timestamp, recvWindow): https://developers.binance.com/docs/binance-spot-api-docs/rest-api/request-security
34. Coinbase Exchange — REST API authentication (30-second window): https://docs.cdp.coinbase.com/exchange/rest-api/authentication
35. Kalshi — API keys (RSA-PSS signing, demo host): https://docs.kalshi.com/getting_started/api_keys
36. AWS — Signature Version 4 for API requests: https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_aws-signing.html
37. RFC 6749 §6 — Refreshing an Access Token: https://www.rfc-editor.org/rfc/rfc6749#section-6
38. RFC 9700 — Best Current Practice for OAuth 2.0 Security: https://datatracker.ietf.org/doc/rfc9700/
39. Stripe — API keys (prefixes, sandbox vs live, rotation): https://docs.stripe.com/keys
40. OWASP — Secrets Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
41. OWASP — Logging Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
42. Alpaca — Getting Started with Trading API (X-Request-ID): https://docs.alpaca.markets/us/docs/getting-started-with-trading-api
43. RFC 6455 §5.5 — Control frames (Ping/Pong), §7.4.1 status codes: https://www.rfc-editor.org/rfc/rfc6455.html#section-5.5
44. websockets — Keepalive and latency: https://websockets.readthedocs.io/en/stable/topics/keepalive.html
45. websockets — Memory and buffers: https://websockets.readthedocs.io/en/stable/topics/memory.html
46. websockets — Design (backpressure): https://websockets.readthedocs.io/en/stable/topics/design.html
47. Coinbase Exchange — WebSocket feed overview (sequence numbers): https://docs.cdp.coinbase.com/exchange/websocket-feed/overview
48. Coinbase Exchange — WebSocket channels (level2, heartbeat): https://docs.cdp.coinbase.com/exchange/websocket-feed/channels
49. Coinbase Advanced Trade — WebSocket channels (heartbeats): https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-channels
50. Kalshi WebSocket practice notes (seq gaps, resnapshot): https://github.com/eishan05/kalshi-agent-skills/blob/main/websocket.md
51. kalshi-python-sdk issue #189 — orderbook permanently desyncs after a sequence gap: https://github.com/TexasCoding/kalshi-python-sdk/issues/189
52. Antithesis — Deterministic simulation testing: https://antithesis.com/docs/resources/deterministic_simulation_testing/
53. TigerBeetle — Protocol-aware DST: https://tigerbeetle.com/blog/2026-08-20-protocol-aware-dst/ ; FoundationDB simulation notes: https://pierrezemb.fr/posts/diving-into-foundationdb-simulation/
54. VCR.py — Usage (record modes): https://vcrpy.readthedocs.io/en/latest/usage.html
55. VCR.py — Advanced (filters, hooks): https://vcrpy.readthedocs.io/en/latest/advanced.html
56. Shopify — Toxiproxy: https://github.com/Shopify/toxiproxy
57. Pact — How Pact works: https://docs.pact.io/getting_started/how_pact_works
58. Alpaca — Paper Trading: https://docs.alpaca.markets/us/docs/paper-trading
59. Betfair — When should I use the Delayed or Live Application Key?: https://support.developer.betfair.com/hc/en-us/articles/360009638032-When-should-I-use-the-Delayed-or-Live-Application-Key
60. Interactive Brokers — TWS API initial setup (ports 7496/7497, Read-Only API): https://interactivebrokers.github.io/tws-api/initial_setup.html
61. Interactive Brokers — TWS release notes (Gateway ports 4001/4002): https://www.interactivebrokers.com.sg/en/index.php?f=15912
62. SEC — Rule 15c3-5 FAQ (pre-trade risk controls): https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0
