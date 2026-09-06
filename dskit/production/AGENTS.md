# AGENTS.md — `dskit/production`

Orientation for an agent working inside the production layer. Repo-wide rules
live in the root `AGENTS.md`; this file is what is different here.

## What this package is

The serving layer: it takes an immutable **release** of a finished pipeline run
and drives it forward on a cadence — fetch, decide, guard, act, record. It is an
*application* of the toolkit, not part of it, which is why its import rule is
its own (below) and the words "tier 1" are never reused for it.

Design contract: `docs/new_package_proposals/production.md`. §4–§7 are the
contracts; §5.15 is the OOP ruling; §5.16 is the producer table. ADR-0090 and
ADR-0091 are the decisions. **Read the section before you change the code it
specifies** — most of this package is about what must *not* happen, and those
refusals are the specification.

## The import rule (enforced by `tests/production/test_purity.py`)

stdlib + `dskit.pipeline` + `dskit.onboarding` + `dskit.assets` + itself.
`dskit.journal` at **function depth only** (ADR-0056). `pytest` may be named
only inside `executor.py`'s conformance-suite builder, at function depth. Never
anything else, at any depth — a serve process that fails to start on the host it
is meant to guard is the failure this rule exists to prevent.

`dskit/pipeline` must never import `dskit.production`. The dependency arrow
points one way; the pipeline receives a `ReleaseReader` through
`ExecutionPolicy.reader(key)` and calls it only through that handle.

## Conventions that bite

- **Default-deny params.** Every configurable class declares `_PARAMS` and
  refuses the rest through `reject_unknown_params`, imported from
  `dskit.pipeline.node` — never copied.
- **`__all__` plus the `_` prefix is the API contract.** No underscore name is
  exported and no module reaches another module's private name.
- **Closed sets live only in `vocab.py`.** Nothing anywhere else defines one.
  A test enumerates them.
- **No `if kind ==` / `if mode ==` / `if rung ==` chains.** A registry entry, a
  strategy object or a table keyed by the declared value. `compose.py` is the
  ONLY module that may read a rung, and an AST test enforces it.
- **Money is `Decimal`, instants are epoch-ms `int`s.** A float under any
  `vocab.MONEY_FIELDS` name refuses at the ledger boundary, at any depth.
  Dimensionless ratios may be float.
- **Time comes from the injected `Clock`.** Nothing outside `clock.py` calls
  `time.time()`; nothing compares wall stamps to order events.
- **`SeriesState` is the sole ledger fold.** Nothing else folds; `Ledger.append`
  calls `apply`. An AST test pins which module may assign the folded attributes.
- **Docstrings**: NumPy sections, an `Examples` block that instantiates the
  class (`::` blocks, `# ->` for expected values, never `>>>`), types in the
  docstring text, no type hints in signatures. `ruff` runs `D` here.
- **A new document key is OPTIONAL or it moves every identity.** Phase 2's
  `outcomes`, `reporting`, `durability.ledger`, `execution.signer`, the four
  `alerting` keys and the four `readiness` knobs are all optional and all
  graded: absent, they are absent from the hash material, so a phase-1
  document keeps its hash; present, they change a number someone acts on.
  A REQUIRED addition orphans every prior run — see the root `CLAUDE.md`.
- **A knob's default has ONE name.** `report.py`'s `_knob(section, name,
  DEFAULT_X)` is the shape: never `getattr(..., x, <literal>)` in both the
  validator and the user. `alerts.DEFAULT_MAX_SILENCE_S`,
  `outcomes.DEFAULT_OUTCOME_LOOKBACK_MS`, `monitors.DEFAULT_BINS` and
  `monitors.DEFAULT_SCORING` are the others phase 2 added.
- **A module that spells a vocabulary member owes `pin_members`.** It refuses
  at IMPORT when the spelling strays; `exact=True` when a dispatch table is
  keyed by the whole set, so a new member cannot land unhandled.
- **Nothing is overwritten.** An outcome that disagrees with what stands is a
  new record linked to what it supersedes; a suppressed alert is still
  appended with the suppression NAMED in its body. The evidence must survive
  the correction and the suppression alike.

## The safety spine — change these only with the plan open

1. **Record before act, checkpoint last.** Every effect is preceded by its
   barrier: `decision_plan` → barrier → `intent` → barrier → authority records →
   barrier → submit. A reduction inserts `authority_use` before `authorization`.
2. **The fresh fold.** Leg steps (2), (3) and (6) each take a *new*
   `SeriesState.snapshot()`, because an earlier leg of the same tick can trip
   the breaker or add a reservation. Step (6) refuses if any member the plan and
   intent already bound has moved.
3. **`Intent` and `ReductionIntent` are different objects with different
   digests**, and are never spelled alike. `reduction_intent_digest` is what the
   single-use right names; `intent_digest` is the leg's own.
4. **Halt never flattens.** Halting refuses submissions and best-effort cancels;
   flattening is a separate authenticated act that enters `reducing`.
5. **`cash_flow` and `tick.nav` are unrecoverable after the fact.** Every
   economic measure partitions the fold on `external`: `pnl`, `drawdown`,
   `consecutive_losses` and `error_vs_realised` see trading records only, while
   `bankroll_fraction` and `exposure` see the capital base including external
   flows. Get this wrong and an adopted deposit turns a trading loss into
   headroom under a `halt` guard.
6. **An unknown outcome is resolved by querying, never by resending.**
7. **A read-only verb never takes the writer lock.** `report` and `replay`
   open the chain through `ChainLedger.reading(...)`: no flock, no torn-tail
   repair, every write refused by name. An ordinary open would refuse exactly
   while the series was being served, and would write to the series it claims
   not to touch.

## Where things live

`compose.py` builds the seven bundles from the document and is the composition
root, plus the composites that are not bundle members (`outcome_join`);
`loop.py` is the scheduler and owns the ten tick phases; `leg.py` owns the
eight submission steps and is where a permit is minted. `Tick.run` and
`LegPipeline.run` are **final** — the phases and steps are the hooks.

## Extension points

The registry-resolved seam ABCs (one per `§4.3` registry — twenty in phase 1,
plus phase 2's `OutcomeSource`, `Ledger` (`LEDGER_KINDS`) and `Signer`
(`SIGNER_KINDS`)), and `Proposer` and `Measure`. A child implements the
venue executor, its accounting, its
approval verifier and its fenced lease, and references them by
`pkg.module:Class` — and, for a push source, the `StreamTransport`
`feed.py` declares, which is the one seam resolved by class reference
alone: no registry owns stream transports, core ships none, and a bare
name refuses saying so (§5.2.1). Children never subclass `ServeLoop`, `GuardChain` or a
policy. A second STORE subclasses `ChainLedger`, not `Ledger`: the envelope,
the digest, the idempotency index, the durability grade and the writer lock
are one implementation, and the five hooks (`_open`, `_store`, `_sync`,
`_walk`, `_shutdown`) plus `scan` are all a store supplies. A child's venue
signer subclasses `HmacSigner` and supplies `probe_request()` — the one
venue fact core cannot hold. `readiness.Evidence` is the one seam with a plain
TABLE rather than a registry (§5.13.4): a checklist evidence name the series
can prove is an `EVIDENCE_RULES` entry, and there is no `uses` site for one.
`report.ReportEmitter` is the other (§5.13.3): `--format` picks one of exactly
two and no document ever selects a report format, so a registry would add a
§4.3 family nothing selects. `report.DIVERGENCE_FIELDS` is a third TABLE, keyed
on the §6 body FIELD name, with `nondeterminism` as the deliberate default —
an unclassifiable difference must never be absorbed into a named class.
`bundles.ReplayTape` is the seam a replay hands the composition root: DATA
(instants, feed results, id allocations) and never an object, so which objects
a replay runs stays `compose.py`'s decision.

## Testing

`tests/production/` mirrors the modules and `tests/production_libs/` the
five packs. `conftest.py` builds a real synthetic training run over a temp
onboarding root — use it rather than inventing a fixture. No network, no
wall-clock sleeps, `TestClock` everywhere. A test that restates its own literal
is worse than none: assert the refusals. A pack test skips when its library is
absent; core's must never need one.

Gates that must stay green: `test_purity.py` (the import rule and the AST bans),
`test_oop.py` (§5.15), `test_producers.py` (§5.16's closure), and the four
sibling purity gates. The 20 pinned sha256 literals under `tests/` must not
move.

## Directory

```
dskit/production/
├── __init__.py __main__.py            public surface; the 23 CLI verbs
├── base.py vocab.py redact.py         errors + Registry + hashing; closed sets; secrets
├── document.py release.py records.py  the serve document; the release; every value object
├── clock.py sessions.py cadence.py    when a tick may happen
├── feed.py decider.py                 what enters the graph; the re-execution
├── guards.py breaker.py arming.py     what may pass; what stops it; who may arm it
├── executor.py accounting.py verifier.py coordination.py   the act and its gates
├── policy.py control.py resilience.py  the matrices; the inbox; retry/limits/Signer
├── ledger.py state.py reconcile.py     the chain; the sole fold; the venue truth
├── monitors.py metrics.py alerts.py health.py   what watches, counts, pages, probes
├── ids.py bundles.py compose.py leg.py loop.py  ids; the bundles; the root; steps; ticks
├── readiness.py outcomes.py report.py  GO/NO-GO; what happened; what it was worth
├── libs/ (parquet.py sqlite.py         tier-2 packs: RunReference; SqliteLedger;
│        exchange_calendars.py         ExchangeCalendar; PrometheusSink; OtelSink
│        prometheus.py opentelemetry.py)
└── README.md CLAUDE.md AGENTS.md
```

`README.md` carries the same tree one file per line, with what each holds —
keep both current when files are added or removed.
