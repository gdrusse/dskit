# R7 — State, time and lifecycle of a long-running live decision process

Research input for the `dskit.production` design (working name). Domain-neutral:
"venue" below means whatever the executor object talks to; "calendar" means
whatever object answers *may I decide now*.

## 1. Findings by theme

### 1.1 Clock abstraction

**Swap the clock, keep the loop.** Every mature framework achieves backtest/live
parity by injecting the clock rather than branching on mode. NautilusTrader has
one `Clock` base with `TestClock` and `LiveClock` implementations; in a backtest
"a `TestClock` advances time based on incoming market data", in live "a
`LiveClock` reflects actual system time", and the same `set_timer` /
`set_time_alert` / `cancel_timer` API serves both ([actors][nt-actors],
[architecture][nt-arch]). Hummingbot's `Clock(clock_mode, tick_size, start_time,
end_time)` drives every component through `c_start / c_tick / c_stop`;
`REALTIME` sleeps to the next boundary, `BACKTEST` does
`_current_tick += _tick_size` ([clock.pyx][hb-clock], [architecture][hb-arch]).
Rx's `TestScheduler` is the same idea outside trading: "time stands still until
you tell it to move on" ([introtorx][rx-test]). Python's stdlib already ships the
seam: `sched.scheduler(timefunc=time.monotonic, delayfunc=time.sleep)` takes
both functions by injection ([sched][py-sched]).

**Event time vs processing time.** Flink: processing time is "the system time
of the machine that is executing the respective operation"; event time is "the
time that each individual event occurred on its producing device" and "progress
depends on the data itself rather than wall clocks". A `Watermark(t)` "declares
that event time has reached time t … there should be no more elements … with a
timestamp t' <= t"; waiting for completeness costs latency ([Flink][flink-time]).
Akidau: the skew between the two "is not only non-zero, but often a highly
variable function" of infrastructure and data; windowing by processing time
silently mixes periods ([Streaming 101][streaming101]). Lean formalises this as
the *Time Frontier*: the algorithm's `Time` is the boundary past which no data is
visible; a bar is emitted "when its end time passes", so a Friday daily bar is
delivered Saturday 00:00 ([Lean time][lean-time]). NautilusTrader carries both
stamps on every object — `ts_event` (venue time) and `ts_init` (local
creation/receipt) — orders backtest data "by `ts_init` using a stable sort", and
has a `time_bars_timestamp_on_close` switch because open-vs-close stamping is a
real source of off-by-one-bar bugs ([data][nt-data]). Consequence for a decision
loop: the tick is keyed on the **data's as-of** (the frontier), the wall clock
only decides *when to look*.

**Monotonic vs wall time.** PEP 418: a program that "uses the system time to
schedule events or to implement a timeout … may fail … when the system time is
changed manually or adjusted automatically by NTP"; use `time.monotonic()`, and
note `CLOCK_MONOTONIC` "stops while the machine is suspended" ([PEP 418][pep418]).
Freqtrade's `_throttle` measures the iteration with `time.time()` — a
(benign) instance of the anti-pattern ([worker.py][ft-worker]).

**Timezones, DST, leap seconds, skew.** PEP 495: at a fall-back transition the
same local time occurs twice (`fold`), at spring-forward some local times never
occur; `fold` is ignored by timedelta arithmetic — do arithmetic in UTC and
convert at the edges ([PEP 495][pep495]). Leap seconds make "the same time_t
occur twice"; Google smears them linearly over 24 h noon-to-noon, so two hosts
can legitimately disagree by ~0.5 s ([leap smear][smear]). Venues police skew:
Binance rejects a signed request whose timestamp is outside `recvWindow`
(default 5000 ms, max 60000) or ahead of server time, and recommends syncing
against `serverTime` ([Binance][binance-time], [recvWindow][recvwindow]).

### 1.2 Calendars and sessions

`exchange_calendars` models a calendar as *sessions* (trading days) and
*minutes* with `open / break_start / break_end / close`, early closes, and
navigation (`is_open_on_minute`, `next_open`, `next_close`, `minute_to_session`,
`sessions_in_range`); a custom calendar is a subclass declaring
`regular_holidays`, `adhoc_holidays`, `special_closes`, `open_times`, `tz`
([exchange_calendars][xcals]). `pandas_market_calendars` adds `pre`/`post`
sessions and breaks with `schedule()` returning `market_open/market_close`
columns ([pmc][pmc]). Lean's scheduling vocabulary is the cleanest *config*
grammar for "when may I decide": `DateRules.every_day(symbol) / week_start /
month_end …` × `TimeRules.after_market_open(symbol, minutes) /
before_market_close(symbol, minutes) / at(h, m) / every(timedelta)`, with a
default time zone ([Lean scheduled events][lean-sched]); Zipline's
`schedule_function(func, date_rule, time_rule, calendar, half_days)` is the same
shape ([Zipline][zipline]). Betting has no sessions: flumine gates on the
*event* — `market.seconds_to_start`, `market_book.inplay`, a market filter — and
closes markets on settlement ([flumine quickstart][flumine-qs]). 24/7 venues
still have maintenance windows and settlement times, so "always open" is itself
a calendar, not the absence of one.

### 1.3 Scheduling cadence and overruns

Three cadences recur: **fixed interval** (Freqtrade `process_throttle_secs`,
sleep = period − elapsed, capped "at the next candle arrival plus an offset"
([ft-worker][ft-worker], [bot basics][ft-basics])); **aligned to boundary**
(Hummingbot `next_tick = ((now // tick_size) + 1) * tick_size` — drift-free
because it is recomputed from the absolute clock each tick, not by adding the
period to the actual start ([hb-clock][hb-clock])); **event-driven** (Lean fires
`OnData` per time slice; in *backtests* scheduled events fire only when data
arrives — a 2 AM event fires at 9:31 — while in live they fire on a real-time
thread, a documented parity hazard ([lean-sched][lean-sched],
[engine][lean-engine])).

Overrun vocabulary is settled elsewhere: Kubernetes `concurrencyPolicy`
`Allow | Forbid | Replace` plus `startingDeadlineSeconds` (skip a run that
missed its slot by more than N s) ([K8s CronJob][k8s-cron]); APScheduler
`misfire_grace_time`, `coalesce` ("one or more queued executions … trigger it
once"), `max_instances` (a run due while the previous is still running "is
treated as a misfire") ([APScheduler][aps]); systemd timers never queue ("if
the unit to activate is already active … it is not restarted, but simply left
running"), `Persistent=` replays one missed activation after downtime,
`AccuracySec=` defaults to **1 minute** of deliberate jitter ([systemd.timer][sd-timer]).
Stdlib `sched` "falls behind but doesn't drop events" — the caller must cancel
stale ones ([sched][py-sched]).

### 1.4 Crash safety, resumption, reconciliation, locking, shutdown

**Reconcile before deciding.** NautilusTrader's node "aligns cached order and
position state with venue reports before trader components start";
reconciliation or connection failure "abort[s] startup". It consumes
`OrderStatusReport / FillReport / OrderWithFills / PositionStatusReport`,
enforces one fill per `trade_id`, creates *external* orders for reports it does
not know, and — crucially — after a transport error "keeps these in flight
while reconciliation determines actual state through polling or queries",
emitting a rejection "only when the failure is attributable to that command and
proves it was not sent. Otherwise, it logs the failure without inventing an
outcome" ([live][nt-live], [execution][nt-exec]). Its knob set is a checklist:
`reconciliation`, `reconciliation_lookback_mins`,
`reconciliation_startup_delay_secs` (10 s), `inflight_check_interval_ms` /
`inflight_check_threshold_ms` / `inflight_check_retries`,
`open_check_interval_secs`, `position_check_interval_secs`,
`timeout_connection/reconciliation/portfolio/disconnection/shutdown_secs`,
`delay_post_stop_secs` ([configure live][nt-cfg]). Its own issue tracker shows
the failure mode of *adopting* mismatches: synthetic orders on restart produced
duplicates ([issue 3176][nt-3176]). Freqtrade's `startup()` runs
`startup_update_open_orders()` — re-fetch every persisted open order from the
exchange, update trade state, treat >5-day-old orders as cancelled — before the
first `process()`; its loop step 1 is "retrieve persisted open trades" and step
6 "update open order states from exchange" every iteration ([freqtradebot.py][ft-bot],
[bot basics][ft-basics]). Lean's `Initialize` "in live mode, loads existing
holdings and orders" ([lean-engine][lean-engine]).

**Write ordering / unknown outcome.** The transactional-outbox pattern records
intent atomically *before* the side effect so a crash between the two leaves a
recoverable "owed" row ([outbox][outbox]). FIX makes the unknown state first
class: an Order Status Request for an unrecognised order returns
`OrdStatus=U (Unknown)` ([FIX 39=U][fix-u]). Idempotency keys (client order ids)
are what make a retry safe; Alpaca assigns a `client_order_id` if the client
does not and exposes `pending_new` for "received … but not yet accepted"
([Alpaca][alpaca]). dskit already runs this shape: WORM snapshot first,
checkpoint last, at-least-once + hash dedupe.

**Persistence is not continuous.** NautilusTrader's actor state save is "not
continuous checkpointing — the system saves at most once per run" ([nt-cfg][nt-cfg]);
APScheduler warns a persistent store re-adds a job on every restart unless the
job has a stable id and `replace_existing=True` ([aps][aps]) — the loop's
identity must be stable across restarts.

**Single instance.** `flock(2)` is advisory, `LOCK_EX|LOCK_NB` fails fast, and
the lock is released when all descriptors close or the process dies — the
property a bare pidfile lacks; it is inherited across `fork`, and NFS emulates
it via byte-range locks ([flock][flock], [single instance][rednafi]).
`dskit.journal` already has this helper.

**Shutdown and restart policy.** Docker sends SIGTERM then SIGKILL after 10 s
([docker stop][docker-stop]); Kubernetes: preStop → SIGTERM →
`terminationGracePeriodSeconds` (30 s) → SIGKILL, restart back-off 10 s…5 min
([pod lifecycle][k8s-pod]); systemd: `TimeoutStopSec` then SIGKILL; a
`Type=notify` service sends `READY=1`, `STOPPING=1`, `WATCHDOG=1`, `STATUS=`,
and `EXTEND_TIMEOUT_USEC=` to buy time for an in-flight step
([systemd.service][sd-service], [sd_notify][sd-notify]). "Do not restart me"
is expressible everywhere by exit status: systemd `Restart=on-failure` +
`RestartPreventExitStatus=<code>`; Docker `on-failure` and Kubernetes
`OnFailure` never restart exit 0 ([sd-service][sd-service],
[docker restart][docker-restart], [k8s-pod][k8s-pod]). Freqtrade's state
machine is `RUNNING | PAUSED | STOPPED | RELOAD_CONFIG`; an
`OperationalException` moves it to STOPPED ([ft-worker][ft-worker]).

### 1.5 Deterministic replay

Temporal: workflow code re-runs against an immutable event history, so it may
not read the wall clock, randomness, or do I/O outside an Activity; an
Activity "runs once, its result is recorded … During replay, that result is
reused" ([Temporal][temporal]). Fowler: gateways must know they are in replay
— external *updates* are suppressed, external *queries* return the remembered
answer ("I will need the exchange rate on Dec 5, not the later one")
([Fowler][fowler]). vcrpy records HTTP interactions to cassettes with record
modes (`once`, `none`, …) ([vcrpy][vcrpy]). FoundationDB runs "a deterministic
simulation of an entire cluster within a single-threaded process" with simulated
network, disk and time ([FDB][fdb]). flumine's simulation sets
`config.current_time` from `market_book.publish_time` and merges several
recorded streams by epoch — event-time replay through the same strategy hooks
([simulation.py][flumine-sim]).

### 1.6 Common decomposition across frameworks

Every framework separates: **clock** (test/live), **feed** (historical vs
stream), **execution** (simulated/paper vs venue), **cache/state** (orders,
positions, checkpoints), **calendar/gate**, **workers** (keep-alive, polls), and
a **kernel** that owns startup order and coordinated shutdown. NautilusTrader's
component states — `PRE_INITIALIZED, READY, STARTING, RUNNING, STOPPING,
STOPPED, RESUMING, RESETTING, DISPOSING, DISPOSED, DEGRADING, DEGRADED,
FAULTING, FAULTED` — are the fullest list; flumine's `__enter__` (login →
workers → strategies → streams) / `__exit__` (finish strategies → stop workers
and streams → logout) is the minimal one ([nt-arch][nt-arch],
[baseflumine.py][flumine-base]).

## 2. Design implications for dskit

### 2.1 `Clock` ABC (tier 1)

```python
class Clock(ABC):
    @abstractmethod
    def now_ms(self): ...          # epoch-ms UTC; the only wall read
    @abstractmethod
    def monotonic(self): ...       # seconds; timeouts and pacing only
    @abstractmethod
    def sleep_until(self, epoch_ms, wake): ...  # returns early if wake() is True
```

`WallClock` (`time.time_ns`, `time.monotonic`, sleeps in ≤1 s slices so a stop
flag is honoured), `TestClock` (`set(ms)`, `advance(ms)`; `sleep_until` jumps
instantly), `ReplayClock` (a `TestClock` the feed advances to each record's
as-of + `publish_delay_ms`). Every decision record carries **three** times:
`tick_at` (scheduled boundary, event time), `data_asof` (max as-of of inputs —
the watermark), `observed_at` (wall). Nothing in the loop compares wall
timestamps for ordering; only `tick_at` orders ticks.

### 2.2 Calendar gate — config plus injected object

```python
class Calendar(ABC):
    @abstractmethod
    def is_open(self, epoch_ms): ...
    @abstractmethod
    def next_open(self, after_ms): ...
    @abstractmethod
    def next_close(self, after_ms): ...
```

Tier-1 members: `AlwaysOpen`, `WeeklySessions` (config below, `zoneinfo`),
`EventWindow` (open from `start − lead` until `start` or an in-play flag),
`Composite` (intersection with blackouts). Tier-2 pack:
`libs/exchange_calendars.py` adapter. A venue-status calendar (asks the
connector's status endpoint) is a third-tier subclass. Config shape, `uses`
resolved like a node kind:

```jsonc
"calendar": {
  "uses": "weekly_sessions",
  "tz": "Region/City",                       // IANA name; required
  "sessions": [{"days": ["mon","fri"], "open": "09:30", "close": "16:00"}],
  "holidays": ["YYYY-MM-DD"],                 // sorted, unique
  "special_closes": [{"date": "YYYY-MM-DD", "close": "13:00"}],
  "blackouts": [{"from": "<ISO-8601 UTC>", "until": "<ISO-8601 UTC>"}],
  "after_open_s": 0, "before_close_s": 0     // ints >= 0; pre/post buffers
}
```

Sessions are local-time config; boundaries are localised per session date and
converted to epoch-ms once (PEP 495). A session boundary that falls in a DST
gap is a validation error, not a silent shift.

### 2.3 Cadence policy family

```python
class Cadence(ABC):
    @abstractmethod
    def next_tick(self, after_ms, calendar): ...   # first tick > after_ms that is open
```

`FixedInterval(period_ms, anchor_ms)` (drift-free: `anchor + k·period`, never
`last_start + period`), `AlignedBar(bar_ms, publish_delay_ms)` (Hummingbot
boundary + Lean end-time convention: tick at `bar_close + delay`),
`AtTimes(["09:35", "15:55"], relative="open|close|clock")` (Lean `TimeRules`),
`OnData(poll_ms)` (tick when the connector reports a new as-of; the poll is the
`watch` interval). Overrun is a separate strategy object, not a flag on the
loop: `Overrun.skip | coalesce | queue` with `max_lag_ms` (K8s
`startingDeadlineSeconds` / APScheduler `misfire_grace_time`); default
`coalesce` — one catch-up tick at the *latest* due boundary, and the record
notes the ticks it absorbed. Never `Allow` — concurrent ticks would race the
executor.

### 2.4 Loop lifecycle state machine

`INIT → LOCKED → RECONCILING → READY → {WAITING ⇄ TICKING} → STOPPING →
STOPPED`, plus `HALTED` (kill-switch; refuses to restart) and `FAULTED`
(unexpected exception; restartable). A tick runs fixed phases as hooks on an
abstract `Tick` (subclass to extend, never `if mode ==`):

1. **gate** — `calendar.is_open(tick_at)`; closed ⇒ record `skipped:closed`.
2. **fetch** — `run_acquisition(..., mode="live")` through the connector seam;
   read the resulting snapshot's as-of.
3. **watermark** — refuse if `tick_at − data_asof > max_staleness_ms`
   (record `skipped:stale`); also refuse if `|venue_time − now| >
   max_venue_skew_ms`.
4. **decide** — run the pipeline document with the same `Node` objects at
   `asof = tick_at`; produce one decision.
5. **record intent** — append decision record `status=intended`,
   `key = sha256(loop_id, tick_at, decision)`.
6. **act** — `executor.submit(decision, key)`.
7. **record outcome** — `acked | rejected | unknown` with the executor's
   reference.
8. **checkpoint** — LAST, atomic (`atomic_write_text`).

Checkpoint fields: `loop_id`, `config_hash`, `last_tick_at`,
`last_completed_tick_at`, `pending` (intent keys with no terminal outcome),
`positions_snapshot_at`, `halt` (`null` or `{reason, at}`), `schema_version`.

### 2.5 Write ordering and the executor contract

Record-before-act, checkpoint-last (the acquisition rule, extended). The
executor is the only side-effect gateway:

```python
class Executor(ABC):
    @abstractmethod
    def submit(self, decision, key): ...     # idempotent on key
    @abstractmethod
    def query(self, key): ...                # acked|filled|rejected|unknown
    @abstractmethod
    def cancel(self, key): ...
    @abstractmethod
    def snapshot(self): ...                  # positions/open orders/balances
    def venue_time_ms(self): return None     # optional skew probe
```

Rule: a `submit` that raises after the request may have left the venue in an
unknown state — the loop records `unknown` and never re-submits blind; only
`query(key)` may move it, and only the executor may say "proved not sent"
(NautilusTrader's rule). `PaperExecutor` and `RecordedExecutor` implement the
same ABC.

### 2.6 Startup reconciliation contract

Before `READY`: load checkpoint → refuse if `config_hash` differs unless
`--adopt` → `executor.snapshot()` → resolve every `pending` key via `query` →
diff local ledger vs venue → write a `reconciliation` record (always, even when
clean) → apply `on_mismatch`: `halt` (default; exit 3), `adopt` (accept venue as
truth, record the delta), `refuse` (exit 1). Never synthesise venue actions to
"fix" a mismatch (issue 3176). Optional `reconcile_every_s` repeats the diff
between ticks.

### 2.7 Single-instance lock

`flock(LOCK_EX|LOCK_NB)` on `<root>/state/<loop_id>.lock`, held for the
process lifetime, body `{pid, host, started_at}` for humans only. A second copy
exits **4** immediately. Reuse the journal's helper; document the NFS and
fork caveats.

### 2.8 Shutdown semantics

SIGTERM/SIGINT set a stop flag; `WAITING` wakes within 1 s; `TICKING` finishes
the current phase boundary and never stops between phases 6 and 7. Then
checkpoint, release lock, exit **0**. `shutdown_grace_s` must be below the
supervisor's grace (Docker 10 s, K8s 30 s, systemd `TimeoutStopSec`) — refuse
configs that are not. Exit codes: `0` stopped, `3` halted (a halt is a result —
dskit's existing convention), `1` error, `4` already running. A tripped kill
switch writes `halt` into the checkpoint *and* exits 3, so `Restart=on-failure
RestartPreventExitStatus=3` stops systemd, exit 0 alone would stop Docker/K8s
`on-failure`, and a supervisor set to `always` still finds the persisted halt
and refuses (exit 3 again) until `resume` clears it. Optional stdlib
`NOTIFY_SOCKET` support (`READY=1`, `WATCHDOG=1`, `STOPPING=1`).

### 2.9 Replay harness

One `Loop` class; three injected seams. `Feed`: `LiveFeed` (acquire live, read
snapshot) vs `ReplayFeed` (iterate recorded snapshots by as-of, driving
`ReplayClock`). `Clock`: wall vs replay. `Executor`: venue vs paper vs
recorded. A `Recorder` wrapper writes a per-tick *tape*: snapshot ids, every
clock read, every executor response, the RNG seed — Fowler's remembering
gateway, Temporal's history. Replaying a tape with `RecordedExecutor` +
`ReplayClock` must reproduce byte-identical decision records; that is the
backtest-vs-live parity test, because the backtest fold covering `tick_at`
consumes the same snapshot through the same nodes.

### 2.10 Config knobs (identity unless marked ops)

| knob | type / bound | note |
|---|---|---|
| `clock.increment` | existing `epoch|day|week` + new `ms` | give `clock` meaning: the bar |
| `serving.cadence.uses` | `fixed|aligned_bar|at_times|on_data` | |
| `serving.cadence.period_ms` / `bar_ms` | int ≥ 1000 | |
| `serving.cadence.publish_delay_ms` | int ≥ 0 | Lean end-time convention |
| `serving.overrun.policy` | `skip|coalesce|queue` | default coalesce |
| `serving.overrun.max_lag_ms` | int ≥ 0 | |
| `serving.max_staleness_ms` | int ≥ 0 | watermark refusal |
| `serving.max_venue_skew_ms` | int ≥ 0, default 1000 | Binance-style guard |
| `serving.reconcile.on_mismatch` | `halt|adopt|refuse` | |
| `serving.reconcile.every_s` (ops) | int ≥ 0 | |
| `serving.shutdown_grace_s` (ops) | int 1..300 | |
| `serving.lock_path` (ops) | str | |

`schedule` stays hash-excluded provenance; giving `clock` meaning is safe now
because no clocked document has ever run (no hash to orphan).

### 2.11 Tests to write

`TestClock` determinism; `next_tick` alignment across a spring-forward and a
fall-back day per calendar tz; gate refuses holidays/blackouts/buffers;
`FixedInterval` shows zero drift after 10⁶ ticks with slow handlers; overrun
policies; crash injection at every phase boundary (kill between 5–6, 6–7, 7–8)
then restart ⇒ exactly one venue action per key; second instance exits 4;
SIGTERM during sleep exits < grace with a checkpoint; persisted halt refuses
restart; two replays of one tape ⇒ identical records; stale data ⇒
`skipped:stale`; skew probe ⇒ refusal; wall-clock jump backwards in
`TestClock` does not stall pacing (monotonic in use).

## 3. Pitfalls and anti-patterns

- Timeouts or pacing on `time.time()`; ordering ticks by `observed_at`.
- `sleep(period)` after work (drift); `last_start + period` (same); relying on
  systemd timers with default `AccuracySec` for boundary ticks.
- Naive local datetimes; arithmetic across DST in local time; assuming a
  session open exists on every calendar day.
- Assuming a bar is decidable at its close (publish delay); mixing open- and
  close-stamped bars between backtest and live.
- Deciding before reconciling; "fixing" mismatches by synthesising orders.
- Re-submitting after a timeout without an idempotency key; recording the
  outcome before the intent; checkpointing before the ledger is durable.
- Pidfile-existence checks instead of `flock`; locks on NFS.
- Catching SIGTERM and exiting mid-submit; a grace period longer than the
  supervisor's; letting `always` restart a tripped kill switch.
- Memory-only state ("saves at most once per run"); loop identity derived from
  something that changes on restart (APScheduler duplicates).
- Replay that lets gateways reach the outside world, or that reads the wall
  clock or an unseeded RNG.
- Backtest scheduled events that fire "when data arrives" while live fires on a
  clock — the loop must be the only driver in both modes.

## 4. Sources

[nt-actors]: https://nautilustrader.io/docs/latest/concepts/actors/
[nt-arch]: https://nautilustrader.io/docs/latest/concepts/architecture/
[nt-live]: https://nautilustrader.io/docs/latest/concepts/live/
[nt-exec]: https://nautilustrader.io/docs/latest/concepts/execution/
[nt-cfg]: https://nautilustrader.io/docs/nightly/how_to/configure_live_trading/
[nt-data]: https://nautilustrader.io/docs/latest/concepts/data/
[nt-3176]: https://github.com/nautechsystems/nautilus_trader/issues/3176
[lean-time]: https://www.quantconnect.com/docs/v1/key-concepts/understanding-time
[lean-engine]: https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine
[lean-sched]: https://www.quantconnect.com/docs/v2/writing-algorithms/scheduled-events
[ft-basics]: https://www.freqtrade.io/en/stable/bot-basics/
[ft-worker]: https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/worker.py
[ft-bot]: https://github.com/freqtrade/freqtrade/blob/develop/freqtrade/freqtradebot.py
[flumine-base]: https://github.com/betcode-org/flumine/blob/master/flumine/baseflumine.py
[flumine-sim]: https://github.com/betcode-org/flumine/blob/master/flumine/simulation/simulation.py
[flumine-qs]: https://betcode-org.github.io/flumine/quickstart/
[hb-clock]: https://github.com/CoinAlpha/hummingbot/blob/master/hummingbot/core/clock.pyx
[hb-arch]: https://hummingbot.org/blog/hummingbot-architecture---part-1/
[flink-time]: https://nightlies.apache.org/flink/flink-docs-stable/docs/concepts/time/
[streaming101]: https://www.oreilly.com/radar/the-world-beyond-batch-streaming-101/
[xcals]: https://github.com/gerrymanoim/exchange_calendars
[pmc]: https://pandas-market-calendars.readthedocs.io/en/latest/usage.html
[zipline]: https://github.com/stefan-jansen/zipline-reloaded/blob/main/docs/source/api-reference.rst
[pep418]: https://peps.python.org/pep-0418/
[pep495]: https://peps.python.org/pep-0495/
[smear]: https://developers.google.com/time/smear
[binance-time]: https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-api-information
[recvwindow]: https://github.com/tiagosiebler/awesome-crypto-examples/wiki/Timestamp-for-this-request-is-outside-of-the-recvWindow
[py-sched]: https://docs.python.org/3/library/sched.html
[aps]: https://github.com/agronholm/apscheduler/blob/3.x/docs/userguide.rst
[k8s-cron]: https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/
[k8s-pod]: https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/
[sd-service]: https://man7.org/linux/man-pages/man5/systemd.service.5.html
[sd-timer]: https://man7.org/linux/man-pages/man5/systemd.timer.5.html
[sd-notify]: https://man7.org/linux/man-pages/man3/sd_notify.3.html
[flock]: https://man7.org/linux/man-pages/man2/flock.2.html
[rednafi]: https://rednafi.com/misc/run-single-instance/
[docker-restart]: https://docs.docker.com/engine/containers/start-containers-automatically/
[docker-stop]: https://docs.docker.com/reference/cli/docker/container/stop/
[outbox]: https://docs.aws.amazon.com/en_en/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html
[fix-u]: https://www.onixs.biz/fix-dictionary/4.2/app_d24.html
[alpaca]: https://docs.alpaca.markets/docs/orders-at-alpaca
[temporal]: https://docs.temporal.io/workflows
[fowler]: https://martinfowler.com/eaaDev/EventSourcing.html
[vcrpy]: https://vcrpy.readthedocs.io/en/latest/usage.html
[fdb]: https://apple.github.io/foundationdb/testing.html
[rx-test]: https://introtorx.com/chapters/testing-reactive-extensions-for-dotnet
