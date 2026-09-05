# CLAUDE.md — `dskit/production`

Orientation for an agent working inside the production layer. Repo-wide rules
live in the root `CLAUDE.md`; this file is what is different here.

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

## Where things live

`compose.py` builds the seven bundles from the document and is the composition
root; `loop.py` is the scheduler and owns the ten tick phases; `leg.py` owns the
eight submission steps and is where a permit is minted. `Tick.run` and
`LegPipeline.run` are **final** — the phases and steps are the hooks.

## Extension points

The twenty registry-resolved seam ABCs (one per `§4.3` registry) plus `Proposer`
and `Measure`. A child implements the venue executor, its accounting, its
approval verifier and its fenced lease, and references them by
`pkg.module:Class`. Children never subclass `ServeLoop`, `GuardChain` or a
policy.

## Testing

`tests/production/` mirrors the modules. `conftest.py` builds a real synthetic
training run over a temp onboarding root — use it rather than inventing a
fixture. No network, no wall-clock sleeps, `TestClock` everywhere. A test that
restates its own literal is worse than none: assert the refusals.

Gates that must stay green: `test_purity.py` (the import rule and the AST bans),
`test_oop.py` (§5.15), `test_producers.py` (§5.16's closure), and the four
sibling purity gates. The 20 pinned sha256 literals under `tests/` must not
move.

## Directory

See the tree in `README.md` — keep both current when files are added or removed.
