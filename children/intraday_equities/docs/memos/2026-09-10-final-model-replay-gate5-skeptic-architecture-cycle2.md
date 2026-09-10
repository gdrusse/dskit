## Skeptic review — Gate 5 development replay (architecture lens, cycle 2)

**Reviewer task:** Gate 5 stateful replay / fill-policy, reviewer 2 of 2,
re-review after cycle-2 (`358cba6`)
**Model/effort:** Cursor Grok 4.6
**Lens:** architecture / governance / tiering / OOP / file-ownership ONLY.
Whether individual fill-timing unit tests can fail is reviewer 1's method
lens. This review does not re-litigate that. It hunts parallel engines,
ServeLoop composition lies, ADR self-authorization, config-as-interface
theater, tiering, trees, path.csv, and §11 inference.
**Dispatch mode:** sequential — reviewer 2 of 2, independent of the method
lens, over `1b63628..358cba6` on `cursor/gate5-replay-3bda`. Round-1
architecture FAIL:
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture.md`.

## TL;DR

**FAIL — 1 Critical, 2 Major, 4 Nit.**

Cycle-2 renamed the clock (`TestClock` → `ReplayClock`), rewrote notes /
ADR Consequences to say "not ServeLoop", and added `if knob == only-legal-member`
pins. Honesty does not delete an engine. `DevelopmentReplay.run` still
schedules `ReplayAdapter.replay`, which still walks bars itself, constructs
its own `ReplayClock()` per symbol, folds lots in `HorizonBook`, and calls
`PaperExecutor.submit` with `digest = "a"*64` and `account=None`. AST:
`ServeLoop` is never called. Plan of the only run document is still one
node, `inputs: {}`, `edges: []`. The private bar-walk grew (halt catch-up).

## Scope and commits reviewed

Base `1b63628` (round-1 skeptic memos) through `HEAD` (`358cba6`). Cycle-2
is one commit on top of `c286897` (the engine) + `1b63628` (round-1 FAIL
memos):

```text
358cba6 Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai>
  fix(gate5): fail-closed tape, halt, and live fill-policy knobs
```

Author is the exact model (not a family name). Commit body admits
"Synthetic driver is not ServeLoop composition (Phase 5 items 3-5
open)" and then ships more of that driver. Kickoff read from
`origin/claude/dskit-merge-reviewed-branches-de0z8z:children/intraday_equities/docs/plans/2026-09-10-gate5-replay-kickoff.md`.
Plan §3 / §5 / §6 Phase 5 / §8 / §9 lens 7, ADR-0114 Phase 5, ADR-0117
(post-cycle-2 text), Gate 5a architecture memo, current `replay.py`,
`configs/run-development-replay.json` notes, both child trees, journal
row A18870. Round-1 architecture memo is the prior verdict, not a
finding to rubber-stamp.

What I tried to accept and could not: (1) node/ADR notes now say "not
ServeLoop"; (2) `ReplayClock` instead of `TestClock`; (3) overlap knobs
are "read"; (4) Status stayed `proposed` and Consequences dropped
"Implementation may proceed". None of those compose `ServeLoop`, inject
a clock from `compose.py`, or retire the bar-walk.

## Commands run and results

```text
$ git diff --stat 1b63628..358cba6
 .../configs/run-development-replay.json            |   2 +-
 .../docs/decisioning/README.md                   |   2 +-
 .../docs/decisioning/actions.csv                 |   1 +
 .../intraday_equities/replay.py                   | 132 +++++++++++++++++----
 .../tests/test_replay.py                         | 103 +++++++++++++++-
 docs/architecture/decision-log.md              |  19 ++-
 6 files changed, 221 insertions(+), 38 deletions(-)

$ git diff 1b63628..358cba6 -- dskit/production \
    docs/decisioning/path.csv \
    children/intraday_equities/docs/decisioning/path.csv
# empty (0 bytes) — production and both path.csv files untouched

$ git diff origin/claude/phase1-recovery-seven-gates-ao4zdj...358cba6 \
    -- dskit/production docs/decisioning/path.csv \
       children/intraday_equities/docs/decisioning/path.csv
# empty (0 bytes) over the full Gate 5 span as well

$ git diff --diff-filter=A --name-status 1b63628..358cba6
# empty — cycle-2 added no files

$ git log -1 --format='%an <%ae>' 358cba6
Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai>

$ PYTHONPATH=/workspace:/workspace/children/intraday_equities \
    python3 -m dskit.pipeline validate configs/run-development-replay.json \
    --adapter intraday_equities
OK — configs/run-development-replay.json
  name:  intraday-equities-development-replay
  nodes: 1  sections: —
  hash:  fa648c6b04b289f8533337312caf29efcc33aee3e78fdf709b03b3fbb39411f4

$ PYTHONPATH=... python3 -m dskit.pipeline plan configs/run-development-replay.json \
    --adapter intraday_equities
{"document_hash": "fa648c6b…", "order": ["replay"],
 "nodes": {"replay": {"inputs": {}, ...}}, "edges": []}
```

AST of `replay.py`: `ServeLoop` call count **0** (name appears only in the
module docstring). Imports: `dskit.production.clock.ReplayClock`,
`.executor.PaperExecutor`, `.records`, `.state.TickState`. Not `loop`,
`feed`, `compose`, `ledger`, `accounting`, `bundles`. `ReplayTape` count
0. `ReplayFeed` only in the docstring. `ReplayClock()` is constructed
inside `_replay_symbol` (line 393), then `for index, bar in
enumerate(seq)` (line 397) drives `clock.time.set(bar["asof_ms"])`.

Python proof that overlap JSON cannot select another rule (load
`fill-policy.json`, flip one member, `FillPolicy(...)`):

```text
same_lead_overlap='override'           → must be one of ['refuse']
different_lead_overlap='replace'       → must be one of ['concurrent']
same_tick_order='entries_then_exits'  → must be one of ['exits_then_entries']
forced_exit_horizon_basis='decision'   → must be one of ['fill']
halt_handling='queue'                 → must be one of ['skip']
```

After a successful `FillPolicy` construction, those five attributes are
invariably the one legal member. The cycle-2 `else: raise ConfigError`
arms are unreachable through the public constructor. `mark_source` and
`forced_exit_at` have **zero** `Attribute` loads in `replay.py` (vocab pins
only). Tests still call `ReplayAdapter.replay`, never `ServeLoop`
(`test_replay.py` has 17 `ReplayAdapter` call sites, 0 `ServeLoop`).

## Findings

### Critical — `ReplayAdapter` is still the shipped scheduler, beside ServeLoop

Gate 5a architecture PASS: no missing generic hook; children must not
subclass `ServeLoop`. Kickoff: `replay.py` "subclass or compose
`dskit.production`, never own clocks/ledgers/account folds/generic
performance math." ADR-0114 / plan §5: reuse `ServeLoop` + `ReplayFeed` +
replay clock + paper executor + ledger; do not create `backtest.py` as a
parallel engine. Plan §3 is one graph (features → bundle → caps → MIO →
executor → **one account + event ledger**). Production: `compose.py` is
the composition root; `ServeLoop` is the scheduler; `ReplayTape` is DATA;
`ReplayClock` and `ReplayFeed` share one `ManualTime` (`compose.py` ~416–426);
`SeriesState` is the sole ledger fold.

Cycle-2 did **not** compose that graph. It relabeled the private loop.

What still ships in `_replay_symbol` (`replay.py` 348–436, 541–543):

- `clock = ReplayClock()` — a fresh clock **per symbol**, not
  `compose.clock()` sharing `ManualTime` with a `ReplayFeed`. Production
  `clock.py`: `ReplayClock` is "a clock the schedule and the replay feed
  drive between them." Here the child's `for index, bar` loop *is* the
  schedule, poking `clock.time.set`. Substituting `ReplayClock` for
  `TestClock` is not ServeLoop. It is still a hand-driven instant under
  a private walk.
- `venue = PaperExecutor(...)`, `book = HorizonBook(policy)`,
  `digest = "a" * 64`, then `for index, bar in enumerate(seq)`.
- `TickState(..., account=None, ...)`. Submission skips `LegPipeline`.
- `HorizonBook._lots` remains a second position fold keyed by
  `(symbol, lead)`, created per symbol, never applied to `SeriesState`.
  That book is the replay's account. Production already owns the fold.
- Halt catch-up (`expiry_index <= index`, skip then fire on the next
  non-halt bar) is **new scheduler behavior** in this same loop. Cycle-2
  grew the parallel engine; it did not retire it.

`DevelopmentReplay.run` (line 692) still returns
`ReplayAdapter(self._policy).replay(...)`. That is the only node in
`configs/run-development-replay.json`. `plan` of that document: one node,
empty inputs, no edges. Plan §3's graph is absent.
`test_configs.py` still exempts the file from market-run graph pins
(`_NON_MARKET_RUN_DOCS`).

Calling `PaperExecutor.submit` from a private `enumerate(seq)` loop is
not composing production. Relabeling it a "synthetic policy driver" in
the module docstring, node notes, ADR Consequences, and journal A18870
does not change the object graph. A second project would not copy this;
it would use `ServeLoop`. That is still the tiering defect: the generic
tick loop lives in `dskit.production` and is re-owned in the child.

ADR-0117 **Files** still claims `replay.py` "composes `PaperExecutor` +
injected clock; it does not own clocks, ledgers, account folds." The code
constructs the clock, owns `HorizonBook`, and injects nothing from
`compose`. Consequences now *denies* ServeLoop composition — so the ADR
no longer lies that this *is* ServeLoop — while Files still lies that
clocks/folds are not owned. Honesty about "not ServeLoop" while shipping
the forbidden second engine is the round-1 Critical, not a fix.

Document-level `run-development-replay.json` notes still advertise "find
code and accounting defects." Node notes now say "not a production
account." Phase 5 items 3–5 still cannot be expressed on this object
graph. Naming that gap in the ADR does not make this document a Phase 5
replay.

### Major — proposed ADR-0117 still licenses the unaccepted overlap book

Kickoff: write the phase ADR and **get it accepted before implementation
continues past config-shape tests.** Plan §1: do not write implementation
code until the required ADR is accepted. ADR-0117 **Status: proposed**
(2026-09-10). Cycle-2 did not flip Status (correct as a status line; this
is not a self-accepted ADR).

Round-1 Major was Consequences: "Implementation may proceed against
synthetic caps only." Cycle-2 deleted that sentence. Replacement:
"Synthetic tests of the fill/overlap book may run against
development-only caps." That is still a waiver of the kickoff's
acceptance gate: the overlap/expiry/halt-catch-up rule is **proposed,
not ruled**, and the tests that "may run" *are* the parallel engine.

Cycle-2 then implemented new proposed policy (halt-skipped due lots fire
on the next non-halt bar; `expiry_index <= index`) in both the ADR text
("Still proposed") and the bar-walk. That is implementation continuing
past config-shape tests while Status is proposed. Kickoff on overlap:
propose the rule in the ADR for acceptance; "do not silently invent it in
code without it being in the ADR for acceptance." Putting it in the ADR
as proposed and then shipping it is the same unauthorized build, with
the halt-catch-up rule added this cycle.

ADR-0114 item 5/6 pointers remain accurate as pointers. They do not
authorize shipping unaccepted mechanics, including the cycle-2
halt-catch-up extension.

### Major — overlap/expiry knobs still do not dispatch; JSON pins Python policy

Kickoff / ADR-0117 Decision (owner-ruled shape): every fill-model value is
a named JSON field; "never a Python literal a future change requires
editing code to reach."

Cycle-2 added live `==` checks for `same_lead_overlap`,
`forced_exit_horizon_basis`, `same_tick_order`, `different_lead_overlap`,
and `halt_handling`. That is not dispatch of a config vocabulary:

- `_VOCAB` still has **one** member per overlap/expiry/halt/order knob.
  Changing JSON to another member raises at `FillPolicy` load (proved
  above). The walk's `else: raise ConfigError` arms cannot fire for any
  object `FillPolicy` will construct.
- `HorizonBook.open_lot` (`replay.py` 243):
  `if self._policy.same_lead_overlap == "refuse" and key in self._lots`.
  After construction the first conjunct is always true. The live rule
  is refuse-if-open. There is no `override` branch.
- `different_lead_overlap` is asserted **after** the per-symbol walk
  (`replay.py` 424–427). Concurrent-leads is the dict keyed by
  `(symbol, lead)` regardless. The knob does not choose a rule; it
  refuse-closes a value the constructor already made impossible.
- `halt_handling == "skip"` (`replay.py` 400) has **no** `else: raise`.
  If `_VOCAB` ever grew, a halted bar would fall through into
  exits/entries (fail-open). `same_tick_order` fail-closes; halt does
  not. Either way, JSON cannot select `queue`.
- `mark_source` and `forced_exit_at`: zero attribute loads. Closed
  vocabularies that the engine never reads.

Numeric `fill_bar_offset` / `fill_price_field` *are* read (method lens;
not re-litigated). That does not make overlap a config interface.

Cycle-2 Consequences now admits a different ruling "changes `_VOCAB` and
the book, not a silent code default." That is honesty that the kickoff's
config-only change is **not** what shipped. Admitting the defect in the
ADR does not make the knobs dispatch.

`deployment_eligible=false` and `caps=development-only` remain enforced in
`DevelopmentReplay.validate_params`. Digest pin still matches
`FillPolicy.digest()`. Those parts of the interface hold.

## Nits

- `_HashView` is still duplicated in `replay.py` and `tests/test_replay.py`.
- Child README tree still names `replay.py` and both configs, still omits
  `nodes_capital.py` (modified in `c286897`, not this cycle). AGENTS /
  CLAUDE configs-line still names `fill-policy` and not
  `development-replay` (README configs-line does name both). Cycle-2 did
  not touch the trees.
- `fills.sort` still uses `0 if row["kind"] == "exit" else 1` (`if kind==`
  inside the private engine). Extra `Co-authored-by: gdrusse` trailer
  remains on `358cba6`; author line itself is Cursor Grok 4.6.
- `cost_model` is `import_ref`'d then constructed with
  `SchwabCostModel._PARAMS` and `_VOCAB` still allows only that one class.
  The import is not a seam.

## Items checked, not defective under this lens

- **`dskit/production`:** zero-byte diff on cycle-2 and on the full Gate 5
  span, including README/CLAUDE. Correct given Gate 5a (no generic hook).
  Empty production diff is **not** composition; it is evidence the child
  still routes around the loop (the Critical).
- **`path.csv`:** repo-root and child, empty diff. Journal A18870 is
  `next_id = max+1` after A18869; README latest-10 slide only. Agents did
  not edit Path.
- **ADR-0117 Status:** remains `proposed`. Not flipped to accepted.
- **New files this cycle:** none. Gate 5 span's `replay.py`,
  `fill-policy.json`, `run-development-replay.json`, `test_replay.py` were
  kickoff/plan-named (sibling fill-policy still authorized because Phase 3
  `capital-policy.json` does not exist). Cycle-2 did not add an
  unrequested file.
- **SchwabCostModel** still extracted in the child and imported (not
  copied). Equity-domain cost stays in the child.
- **No `__all__` leak of `_` names.** No `NotImplementedError` seam. Child
  production imports (`ReplayClock`, `PaperExecutor`, `Intent`, `Quote`,
  `SimulatedPermit`, `TickState`) are public `__all__` names — used to
  route around `ServeLoop`, which is the Critical, not a private-import
  nit.
- **§11 items 4, 7, 8, 9, 10:** not silently ruled. Caps forced
  `development-only`; Schwab rates in JSON remain labeled illustrative;
  monitors / MIO remaining knobs / reporting-alert numerics untouched.
  Item 5/6 *shape* is owner-ruled in the kickoff; precise overlap and the
  cycle-2 halt-catch-up rule stay proposed (see Majors), not a quiet extra
  §11 close of 4/7/8/9/10.
- **`deployment_eligible`:** refused unless JSON false. Not a
  deployment-eligible replay.
- **Node notes / module docstring / ADR Consequences** no longer claim
  "one graph, one account" or ServeLoop composition. That honesty is
  real. It does not retire the engine (Critical).

## What this lens did not review

Fill-timing arithmetic, halt/expiry edge cases, fee cents, and whether
each unit test can fail — reviewer 1, method lens. This review used those
tests only as evidence of what engine they exercise (`ReplayAdapter.replay`,
not `ServeLoop`). `test_fill_price_field_close_fills_at_close_not_open`
proves one numeric knob is live; it is not overlap dispatch.

## Handoff

FAIL. Do not merge this as Gate 5 production-parity replay. Cycle-2 is a
honesty-and-fail-closed patch on a parallel engine. Rebuild so equity
policy (bar choice, next-bar-open quotes, `(symbol, lead)` identity,
Schwab costs) is injected into `compose` / `ServeLoop` / `ReplayTape` /
`PaperExecutor` / the ledger; delete the private bar-walk, including the
new halt-catch-up walk. Keep ADR-0117 `proposed` until the owner accepts
the overlap/halt-catch-up rule — and do not keep shipping that rule as the
only legal runtime behind a one-member `_VOCAB`. Phase 5 items 3–5
remain unbuilt and have no honest home on the current object graph.
`TestClock` → `ReplayClock` is not that rebuild.

**Verbatim verdict:** `FAIL — 1 Critical, 2 Major, 4 Nit.`
