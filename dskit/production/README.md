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

Eighteen sections are **graded** — they are the document's identity. Four are
excluded (`alert_endpoints`, `heartbeat`, `placement`, `env`), because where a
notification goes and where files land say nothing about what the process
decides. `validate` refuses a top-level key in neither list, so the partition
cannot drift. Note that alerting is split deliberately: the *routes and sink
kinds* are graded (emptying them silences paging as effectively as deleting
them), only the endpoint values are excluded.

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
torn-tail recovery; `SeriesState`, the sole fold; checkpoints; the durable
control inbox; reconciliation and authenticated adoption.

**Observation** — operational, stream, distribution, outcome and parity
monitors; alert sinks and a router; a health state machine with probes and an
external dead-man heartbeat; a metrics registry with closed label sets.

## Directory

```
dskit/production/
├── __init__.py        public surface (curated re-exports)
├── __main__.py        the 17 CLI verbs
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
