# dskit.production

**The production layer**: serve an immutable *release* forward on a cadence,
guard every proposal, make moving money an explicit authenticated act, and
record the whole decision stream in a hash-chained append-only ledger.

A **serve document** (JSON, its own identity hash) declares the process: which
run it serves, where live rows enter that run's own node graph, which node keys
are the decision heads, how head outputs become domain-neutral proposals, how
ticks are scheduled, which guards every proposal must pass, which executor
acts, which accounting evidence limits use, which fenced lease owns submit,
which monitors watch the stream, and where the ledger lives. One `ServeLoop`
runs it.

**The decision is a re-execution of the run's own subgraph.** The loop derives a
serving document from `<run-dir>/config.json` — trainables flipped to `load`
against their pinned artifacts, search winners applied, gate verdicts replayed,
training splits dropped — so the number that reaches the venue is the number the
backtest scored. The loop reads configs; it never restates them.

## The 60-second path

```bash
python -m dskit.production validate examples/production/serve-shadow.json
python -m dskit.production plan     examples/production/serve-shadow.json
python -m dskit.production ready    examples/production/serve-shadow.json
python -m dskit.production serve    examples/production/serve-shadow.json --once
python -m dskit.production status   examples/production/serve-shadow.json
python -m dskit.production verify   examples/production/serve-shadow.json
```

Then score what it decided, and prove it would decide the same again:

```bash
python -m dskit.production outcomes examples/production/serve-scored.json --asof 2026-01-02T00:00:00Z
python -m dskit.production report   examples/production/serve-scored.json --format markdown
python -m dskit.production replay   ./serve/<series-id> --strict
python -m dskit.production ack      <doc> --fingerprint <fp> --proof ack.json
python -m dskit.production silence  <doc> --matcher severity=warning --until 2026-01-02T00:00:00Z --proof s.json
python -m dskit.production approve-hold <doc> --guard day_loss --scope aggregate --proof h.json
```

`report` and `replay` are read-only: they open the chain through
`ChainLedger.reading(...)`, so they take no writer lock and are safe to run
against a series being served. The other four queue through the durable control
inbox and the loop applies them — `outcomes` unauthenticated (it only asks the
declared sources what they found), `ack`, `silence` and `approve-hold`
authenticated, each granting exactly the one thing it names.

`plan` writes the immutable release — run, artifacts, resolved classes and code,
adapter, feed spec, source config, approval and lease fingerprints, the
readiness checklist digest and the complete runtime fingerprint — and
`release_hash` is the sha256 of that manifest. Everything downstream names it:
arming, intents, permits, process records. Change anything that can affect data,
decisions, permissions, durability, valuation or actions and you get a new
release; relocating storage or notification endpoints does not.

Exit codes: **0** stopped · **1** error · **3** halted · **4** already running ·
**5** refused (a readiness NO-GO, or a control verb the series state forbids).

## The four rungs

`shadow` → `paper` → `live_limited` → `live`. They differ **only by which
objects were injected** — that symmetry is the replay-parity guarantee. There is
no `if rung ==` anywhere in the package except `compose.py`, whose one job is to
read it.

Reaching a live venue additionally requires a recorded, expiring,
independently authenticated **maker-checker arm** bound to the release hash,
plus `--armed` and `DSKIT_PRODUCTION_ARM=<release hash>`. Absent or expired
arming records `not_armed` and refuses; it never silently changes the executor
or the rung. Query, reconcile and cancel stay available without arming.

## Writing a serve document

Default-deny at every level, `notes` allowed everywhere, and every threshold is
a document knob — the code holds only named defaults.

```jsonc
{
  "name": "yourproject-serve",
  "series_id": "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1",  // operator-issued UUID, stable across releases
  "rung": "paper",
  "serving": {
    "run_dir": "pipeline_runs/train-2026-01-01-abcd1234",
    "adapter": "yourproject",
    "entry": {"node": "bars", "param": "since_ms", "window_ms": 14400000},
    "heads": ["select"],
    "required_universe": "configs/universe-serve.json",
    "proposer": {"uses": "intent-rows", "params": {"output": "picks"}},
    "max_artifact_age": "P30D"
  },
  "guards": {
    "size":     {"uses": "limit", "params": {"measure": "quantity", "bound": {"max": "100"}, "on_breach": "refuse"}},
    "day_loss": {"uses": "limit", "params": {"measure": "pnl", "window": {"calendar": "session"},
                                             "bound": {"min": "-500"}, "on_breach": "halt"}}
  },
  "execution": {"uses": "paper", "params": {"fill_rule": "touch"}, "submit_timeout_ms": 5000}
  // … schedule, accounting, arming, coordination, reconcile, monitors, health,
  //    durability, resilience, lifecycle, readiness, alerting, placement
}
```

Twenty sections are **graded** — they are the document's identity. Four are
excluded (`alert_endpoints`, `heartbeat`, `placement`, `env`), because where a
notification goes and where files land say nothing about what the process
decides. `validate` refuses a top-level key in neither list, so the partition
cannot drift. Note that alerting is split deliberately: the *routes and sink
kinds* are graded (emptying them silences paging as effectively as deleting
them), only the endpoint values are excluded.

### Scoring, reporting and signing — the optional sections

Every key below is **optional**: absent, it is absent from the hash material, so
a document written before them keeps its identity. Present, it changes a number
someone acts on — or what leaves the process — so it is graded.
`examples/production/serve-scored.json` declares all of them at once.

**`outcomes.sources`** — an ordered map of your names to `OUTCOME_SOURCE_KINDS`
selectors; without it `outcomes` has nothing to ask and `report`'s calibration
is empty. `settlement` reads the executor's own settlements (`lookback_ms`, how
far back to ask); `label` reads a derived stream back through the onboarding
observations seam (`source`, `stream`, `key_fields`, `time_field`,
`value_field`, required; `weight_field`, `outcome_kind` — `settled` or `marked`
— and `lookback_ms`). Every declared source is polled at each cut; an answer
that repeats what already stands is dropped, and one that disagrees is appended
as a correction linked to what it replaces — never an overwrite. Declare two
when the venue settles late and a mark is what you can score today.

**`reporting`** — four knobs on `report`, each defaulted by one named constant:
`scoring` (a registered `dskit.pipeline.metrics` name, default `brier`) picks
the calibration rule, `bins` (>= 2, default 10) its resolution, `markouts_ms`
(a list, default none) the horizons attribution marks each fill out to, and
`markout_tolerance_ms` (default 60000) how far from a horizon a quote may be
and still count. Widen the tolerance for a thin book; empty `markouts_ms` when
you have no post-trade quotes to mark against.

**`durability.ledger`** — a `LEDGER_KINDS` selector beside `fsync`. Absent, the
chain stays `jsonl` — a segment per rotation, human-greppable. `sqlite` puts it
in one file with WAL and `synchronous=FULL` pinned and append-only enforced by
triggers; it has no torn tail (a commit is atomic) but cannot rotate, so choose
it when the host is what you distrust and `jsonl` when you want the tape
readable without a client. `fsync` is unchanged by either and still says how
often the writer commits.

**`execution.signer`** — a `SIGNER_KINDS` selector; `hmac` covers the signature
most venues ask for. `key_env` names the *environment variable*, never the
value; `header` and `timestamp_header` are what the venue expects, `algorithm`
and `prefix` are its dialect, and `max_skew_ms` with `probe_every_ms` (required)
bound how stale the clock estimate may be before a `sign` refuses. Core never
calls it — a child's `LiveExecutor` does, because core ships no venue. Point
`time_url` at the venue's clock endpoint and a new venue is a new config.

**`alerting.inhibit` / `escalation` / `max_silence_s` / `max_ack_s`** —
`inhibit` is a list of `{source, target, equal}` label matchers: while a
`source` alert fires, a matching `target` is not *paged* (the `alert` record is
still appended, so evidence survives). Use it when one failure fans out into a
dozen symptoms. `escalation` is a `primary` / `secondary` / `final` ladder of
`{after_s, sinks}`: an alert nobody has acked or silenced climbs one rung per
pass, in order and never skipping. `max_silence_s` and
`max_ack_s` (default 86400 each, bounded 60 … 604800) cap how long a `silence`
or an `ack` may run unreviewed; a bound always applies, because an unbounded
silence is how a page is lost forever.

**`readiness`'s four new knobs** — the thresholds behind the evidence names a
series can prove about itself. `outcome_window` (a duration) and
`min_outcome_coverage` (0 < x <= 1) feed `outcome_coverage`; `max_outcome_age`
feeds `outcome_freshness`; `calibration_monitor` names the monitor
`calibration_current` reads. Each is required only when a checklist item cites
the evidence that reads it — `ready` refuses by name when one is missing, rather
than defaulting a threshold the code has no business choosing. Citing them is
how a live checklist requires the series to show its decisions have been scored.

## Extending it — the seams a child implements

The toolkit never learns a venue. A child ships tier-3 code and JSON:

| seam | what a child supplies | why it cannot be generic |
|---|---|---|
| `SubmittingExecutor` / `LiveExecutor` | `_submit_native`, order types, error codes, dedup, units | the venue API |
| `Accounting` | `value`, `classify`, `snapshot` against real positions | the venue's own books |
| `ApprovalVerifier` | `verify(canonical_bytes, proof, purpose)` | your trust root |
| `Lease` | a fenced, cross-host lease with a monotonic token | your coordination service |
| `Proposer` | `candidates`/`proposals` for a bespoke head shape | your model's output |
| `Measure` | an exposure or risk formula | your risk definition |
| `OutcomeSource` | `poll(legs, at_ms, standing)` for a world only you can read | where your truth settles |
| `Signer` (subclass `HmacSigner`) | `probe_request()` — the venue's clock endpoint | the venue's own dialect |
| `Ledger` (subclass `ChainLedger`) | `_open`, `_store`, `_sync`, `_walk`, `_shutdown`, `scan` | your durable store |

Every one is resolved by `uses` — a registered kind name or a
`pkg.module:Class` reference — so a child registers nothing and references its
classes by path. `executor_conformance_suite(cls, params, quotes)` runs the same
battery against a child's venue subclass that proves `PaperExecutor`.

## What ships

**Clock / calendar / cadence** — `wall`, `test`, `replay`; `always-open`,
`weekly-sessions` (zoneinfo, DST-gap refused at validation), `event-window`,
`composite`; `fixed-interval`, `aligned-bar`, `at-times`, `on-data`; overrun
`skip | coalesce | queue`.

**Decision** — the serving-document derivation, a policy-aware structural
planner that classifies every node before construction, the single deferred
mutable read, exact uniform coverage, and proposers `intent-rows` /
`target-positions`.

**Guards** — one `Limit` class over a registry of seventeen measures ×
window × bound × scope, plus `range`. Verdicts `allow < warn < amend < refuse <
hold < halt`. Every economic measure partitions the fold on `external`, so an
adopted deposit can never turn a trading loss into headroom.

**Safety** — the breaker (`active | reducing | halted`), authenticated arming,
the action and transition matrices, the final verify-and-call gate, fenced
leases, readiness GO/NO-GO. A checklist item may cite what the SERIES can
prove — `outcome_coverage`, `outcome_freshness`, `calibration_current` — so a
live series is required by its own checklist to show its decisions have been
scored. A guard's `hold` is a §6 record appended by the leg inside step (4)'s
barrier, so the refusal survives a restart; it ends early only through the
authenticated `approve-hold`, which grants nothing beyond ending it.

**Recording** — the JSONL hash-chained ledger with barriers, segments and
torn-tail recovery, or the same chain in one `sqlite` file; `SeriesState`, the
sole fold; checkpoints; the durable control inbox; reconciliation and
authenticated adoption.

**Observation** — eighteen monitors: operational (`staleness`,
`decision_rate`, `coverage`, `latency`, `refusals`), stream
(`page_hinkley`, `tracking_signal`, `ddm`, `adwin`), distribution (`psi`,
`ks`, `jensen_shannon`, `linf`), outcome (`calibration`, `brier`, `skill`,
`prediction_bias`) and `parity`, over reference populations `leading` /
`rolling` / `snapshot` / `run`; alert sinks, a router with inhibition,
silences, acks and an escalation ladder; a health state machine with probes
and an external dead-man heartbeat (file, url or systemd); a metrics registry
with closed label sets.

**Scoring itself** — `outcomes` joins what happened onto each leg through the
declared sources and appends it as a supersede chain; `report` prints
attribution, calibration and the value curve at an explicit cut; `replay`
re-runs a recorded series through recorded objects and diffs every field,
classifying each difference or leaving it `nondeterminism`. All three are
read-only and take no writer lock.

## Directory

```
dskit/production/
├── __init__.py        public surface (curated re-exports)
├── __main__.py        the 23 CLI verbs
├── base.py            ProductionError; Registry; canonical bytes/hash; record hashing
├── vocab.py           every closed vocabulary, one module
├── redact.py          secrets resolution; redact() on logs, alerts and reasons
├── document.py        ServeDocument; default-deny; the graded/excluded partition
├── release.py         ReleaseManifest; ReleaseReader; class/code/runtime fingerprints
├── records.py         every value object: proposals, intents, permits, evidence,
│                      Silence/AlertAck + the matcher rules alerts.py shares
├── clock.py           Clock ABC; WallClock, TestClock, ReplayClock
├── sessions.py        Calendar ABC; AlwaysOpen, WeeklySessions, EventWindow, Composite
├── cadence.py         Cadence ABC; FixedInterval, AlignedBar, AtTimes, OnData; Overrun
├── control.py         ControlInbox; CommandProcessor
├── feed.py            Feed ABC; ServingContract + FeedSpec; EntrySourceFeed; ReplayFeed
├── decider.py         serving_document(); Decider; ServingExecutionPolicy; proposers
├── guards.py          Guard ABC; GuardChain (+ new_holds, the holds a leg must record, and
│                      approve_hold, the early release of one); Limit; RangeGuard; Measure + registry
├── breaker.py         the breaker, its trips, the kill switch, cooling-off
├── arming.py          ApprovalVerifier ABC; maker-checker proofs; the arming fold
├── executor.py        Executor / SubmittingExecutor; Shadow, Paper, Recorded, Live
├── accounting.py      Accounting ABC; PaperAccounting; RecordedAccounting
├── coordination.py    Lease ABC; ProcessLease; LeasePermit; fencing tokens
├── policy.py          ActionPolicy; TransitionPolicy; the composed rule sets
├── verifier.py        SubmissionVerifier — the final verify-and-call gate
├── resilience.py      Classifier; Retry; CircuitBreaker; RateLimiter; Transport; Signer + HmacSigner
├── ledger.py          Ledger ABC + LEDGER_KINDS; ChainLedger (the chain itself); JsonlLedger;
│                      Checkpoint; ServeRoot; chain + verify
├── state.py           SeriesState (the sole fold, silences/alert_acks included);
│                      StateView; PositionBook; Recovery
├── reconcile.py       Reconciler; breaks; adoption; LedgerHistory
├── monitors.py        Monitor ABC; reference/chunker/threshold strategies; families
├── metrics.py         counter/gauge/histogram registry; closed labels; JSONL flush
├── alerts.py          AlertSink ABC; Log/Memory/Email/Webhook; AlertRouter; InhibitRule
│                      (silences and acks are read from the fold, never a second store)
├── health.py          health state machine; probes; heartbeat (file/url/systemd, plus the
│                      ready()/stopping() lifecycle hooks); instance lock; signals
├── ids.py             IdSource ABC; ReleaseIdSource; RecordedIdSource
├── bundles.py         the seven frozen collaborator bundles
├── leg.py             LegPipeline (the eight submission steps; step (4) records a guard's hold);
│                      the Authority family
├── compose.py         bundles_for(); the AuthorityTable; the one rung reader
├── loop.py            ServeLoop (the scheduler); Tick (the ten phases)
├── readiness.py       release-bound checklist → GO / NO-GO; Evidence + EVIDENCE_RULES,
│                      the names the series proves for itself (coverage, freshness,
│                      calibration)
├── outcomes.py        forward_asof; OutcomeSource + registry; OutcomeJoin (D21)
├── report.py          Report (attribution / calibration / value, each at an explicit
│                      cut); ReportView + ReportEmitter ABC + Markdown/Json; Tape +
│                      Replay + ParityDiff — D20's parity, run against a scratch root
├── libs/              tier-2 packs — a library behind a seam this package owns
│   ├── parquet.py     RunReference over a run's predictions, registered as `run`
│   │                  in REFERENCE_KINDS; pyarrow inside the method
│   └── sqlite.py      SqliteLedger — the chain in one file, WAL + synchronous=FULL
│                      pinned, append-only enforced by three triggers, rotate refused;
│                      registered as `sqlite` in LEDGER_KINDS; sqlite3 inside the method
├── README.md          this file
├── CLAUDE.md          agent orientation
└── AGENTS.md          agent orientation (same content, Codex-facing)
```

## Reading further

`docs/new_package_proposals/production.md` is the design contract — §4–§7 are
the sections a test can be written from. ADR-0090 records the package and
ADR-0091 the pipeline seam it needed.
