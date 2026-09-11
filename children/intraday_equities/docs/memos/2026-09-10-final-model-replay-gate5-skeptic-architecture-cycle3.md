## Skeptic review — Gate 5 development replay (architecture lens, cycle 3)

**Reviewer task:** Gate 5 stateful replay / fill-policy, reviewer 2 of 2,
re-review after cycle-3 (`abdb71f`)
**Model/effort:** Cursor Grok 4.6
**Lens:** architecture / governance / tiering / OOP / file-ownership ONLY.
Whether the new bool-halt / duplicate-asof unit tests can fail is reviewer
1's method lens. This review does not re-litigate that. It hunts parallel
engines, ServeLoop composition, ADR self-authorization, config-as-interface,
hardcoded day counts, identity-hash grading, trees, path.csv, and §11
inference.
**Dispatch mode:** sequential — reviewer 2 of 2, independent of the method
lens, over `e191b75..abdb71f` on `cursor/gate5-replay-3bda`. Round-1
architecture FAIL:
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture.md`.
Round-2 architecture FAIL:
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle2.md`.

## TL;DR

**FAIL — 1 Critical, 3 Major, 5 Nit.**

Cycle-3 did not compose `ServeLoop`. Journal A18872 names that gap
"unchanged"; naming remaining work is not a pass. `DevelopmentReplay.run`
still returns `ReplayAdapter(...).replay(...)`, which still walks bars,
constructs `ReplayClock()` per symbol, folds `HorizonBook`, and submits
with `digest = "a"*64` and `account=None`. AST: `ServeLoop` call count
**0**. The private bar-walk is still the scheduler — the round-1/2
Critical. Cycle-3 also shipped a new ungraded tape window:
`_utc_day_end_ms(2)` is a Python day count, not a JSON field, not in
ADR-0117, and not in identity hash `fa648c6b…`.

## Scope and commits reviewed

Base `e191b75` (round-2 skeptic memos) through `HEAD` (`abdb71f`). Cycle-3
is one commit on top of `358cba6` (cycle-2 engine patch) + `e191b75`
(round-2 FAIL memos):

```text
abdb71f Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai>
  fix(gate5): bound fill-only tape, JSON-bool halt, unique asof
```

Author is the exact model (not a family name). Commit body is method-only
("one extra UTC day of fill-only bars, halt flags must be bool, duplicate
timestamps refuse"). It does not claim ServeLoop composition. Kickoff read
from
`origin/claude/dskit-merge-reviewed-branches-de0z8z:children/intraday_equities/docs/plans/2026-09-10-gate5-replay-kickoff.md`.
Plan §3 / §5 / §6 Phase 5 / §8 / §9 lens 7, ADR-0114 Phase 5, ADR-0117
(unchanged this cycle), Gate 5a architecture memo, current `replay.py`,
`configs/run-development-replay.json` notes, both child trees, journal row
A18872. Round-2 architecture memo is the prior verdict, not a finding to
rubber-stamp.

What I tried to accept and could not: (1) A18872 / module docstring /
node notes already say "not ServeLoop"; (2) cycle-3 is a small fail-closed
tape patch; (3) tests are 23/23 green; (4) Status stayed `proposed` and
`path.csv` was untouched. None of those compose `ServeLoop`, inject a
clock from `compose.py`, retire the bar-walk, or put the new day count in
the document that hashes.

## Commands run and results

```text
$ git diff --stat e191b75..abdb71f
 .../docs/decisioning/README.md                   |  2 +-
 .../docs/decisioning/actions.csv                 |  1 +
 .../intraday_equities/replay.py                  | 40 +++++++++++++++++----
 .../tests/test_replay.py                         | 31 +++++++++++++----
 4 files changed, 61 insertions(+), 13 deletions(-)

$ git diff e191b75..abdb71f -- dskit/production \
    docs/decisioning/path.csv \
    children/intraday_equities/docs/decisioning/path.csv
# empty (0 bytes) — production and both path.csv files untouched

$ git diff origin/claude/phase1-recovery-seven-gates-ao4zdj...abdb71f \
    -- dskit/production docs/decisioning/path.csv \
       children/intraday_equities/docs/decisioning/path.csv
# empty (0 bytes) over the full Gate 5 span as well

$ git diff --diff-filter=A --name-status e191b75..abdb71f
# empty — cycle-3 added no files

$ git diff e191b75..abdb71f -- docs/architecture/decision-log.md
# empty (0 bytes) — ADR-0117 Status not flipped; no fill-only text added

$ git log -1 --format='%an <%ae>' abdb71f
Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai>

$ PYTHONPATH=/workspace:/workspace/children/intraday_equities \
    python3 -m dskit.pipeline validate \
    children/intraday_equities/configs/run-development-replay.json \
    --adapter intraday_equities
OK — configs/run-development-replay.json
  name:  intraday-equities-development-replay
  nodes: 1  sections: —
  hash:  fa648c6b04b289f8533337312caf29efcc33aee3e78fdf709b03b3fbb39411f4

$ PYTHONPATH=... python3 -m dskit.pipeline plan .../run-development-replay.json \
    --adapter intraday_equities
{"document_hash": "fa648c6b…", "order": ["replay"],
 "nodes": {"replay": {"inputs": {}, ...}}, "edges": []}

$ python3 -m pytest tests/test_replay.py -q --tb=no
# from children/intraday_equities; 23 passed in 0.22s
```

AST of `replay.py` at `abdb71f`: `ServeLoop` call count **0** (name is
not a Load; it appears only in the module docstring). Imports:
`dskit.production.clock.ReplayClock`, `.executor.PaperExecutor`,
`.records`, `.state.TickState`. Not `loop`, `feed`, `compose`, `ledger`,
`accounting`, `bundles`. `ReplayTape` count 0. `ReplayFeed` count 0.
`ReplayClock()` is constructed inside `_replay_symbol` (line 397), then
`for index, bar in enumerate(seq)` (line 401) drives `clock.time.set`.
`DevelopmentReplay.run` still ends `return ReplayAdapter(self._policy).replay(...)`.
Dummy `digest = "a" * 64` and `TickState(..., account=None, ...)` remain.
`test_replay.py`: 18 `ReplayAdapter` mentions, **0** `ServeLoop`.

Python proof that overlap JSON still cannot select another rule (load
`fill-policy.json`, flip one member, `FillPolicy(...)`):

```text
same_lead_overlap='override'           → must be one of ['refuse']
different_lead_overlap='replace'       → must be one of ['concurrent']
same_tick_order='entries_then_exits'  → must be one of ['exits_then_entries']
forced_exit_horizon_basis='decision'   → must be one of ['fill']
halt_handling='queue'                 → must be one of ['skip']
```

After a successful `FillPolicy` construction, those five attributes are
invariably the one legal member. `mark_source` and `forced_exit_at` still
have **zero** Attribute loads in `replay.py`.

Cycle-3 day-count proof (this cycle's new code):

```text
AST: self._utc_day_end_ms(1)  lineno 675   # exclusive end of evidence_end
     self._utc_day_end_ms(2)  lineno 679   # NEW fill-only window
DevelopmentReplay._PARAMS = (deployment_eligible, evidence_end, fill_policy,
                             fill_policy_sha256, caps)
fill-policy.json: no day / fill_only / window key
run-development-replay.json params: same five names; hash unchanged fa648c6b…
ADR-0117 text: no 'fill-only', 'fill_only', 'extra_days', '+2', 'timedelta'
evidence_end=2025-10-16 (Thursday): exclusive=1760659200000 (2025-10-17T00:00Z)
                                   fill_end =1760745600000 (2025-10-18T00:00Z)
bar at exact fill_end → ConfigError (1 bar at or after fill-only end)
bar at fill_end-1ms   → ACCEPTED, entry fill at that instant
evidence_end=2025-10-17 (Friday): fill_end=2025-10-19T00:00Z
Monday 2025-10-20 13:30Z bar → REFUSED (next session is outside +2 UTC days)
```

Identity hash `fa648c6b…` is the same as cycle-1/2. The new tape window
changes which bars the run will compute over and is not a graded field.

## Findings

### Critical — `ReplayAdapter` is still the shipped scheduler, beside ServeLoop

This is the round-1/2 Critical. Cycle-3 did not claim to close it. A18872
("Architecture ServeLoop gap unchanged") and the module docstring ("it is
not the production scheduler") are honesty about a defect that still
ships. Honesty is not composition.

Gate 5a architecture PASS: no missing generic hook; children must not
subclass `ServeLoop`. Kickoff: `replay.py` "subclass or compose
`dskit.production`, never own clocks/ledgers/account folds/generic
performance math." ADR-0114 / plan §5: reuse `ServeLoop` + `ReplayFeed` +
replay clock + paper executor + ledger; do not create `backtest.py` as a
parallel engine. Plan §3 is one graph (features → bundle → caps → MIO →
executor → **one account + event ledger**). Production: `compose.py` is
the composition root; `ServeLoop` is the scheduler; `ReplayTape` is DATA;
`ReplayClock` and `ReplayFeed` share one `ManualTime` (`compose.py`
416–426); `SeriesState` is the sole ledger fold. `clock.py`: `ReplayClock`
is "a clock the schedule and the replay feed drive between them."

What still ships in `_replay_symbol` (`replay.py` 348–440, 720):

- `clock = ReplayClock()` — a fresh clock **per symbol**, not
  `compose.clock()` sharing `ManualTime` with a `ReplayFeed`. The child's
  `for index, bar` loop *is* the schedule, poking `clock.time.set`.
  Calling the class `ReplayClock` instead of `TestClock` is not ServeLoop.
- `venue = PaperExecutor(...)`, `book = HorizonBook(policy)`,
  `digest = "a" * 64`, then `for index, bar in enumerate(seq)`.
- `TickState(..., account=None, ...)`. Submission skips `LegPipeline`.
- `HorizonBook._lots` remains a second position fold keyed by
  `(symbol, lead)`, created per symbol, never applied to `SeriesState`.
  That book is the replay's account. Production already owns the fold.

Cycle-3's new work (duplicate-asof refuse, JSON-bool halt, fill-only
window) all landed **on this same private walk / doorway**. The scheduler
grew fail-closed behavior; it was not replaced.

`DevelopmentReplay.run` (line 720) still returns
`ReplayAdapter(self._policy).replay(...)`. That is the only node in
`configs/run-development-replay.json`. `plan` of that document: one node,
empty inputs, no edges. Plan §3's graph is absent.
`test_configs.py` still exempts the file from market-run graph pins
(`_NON_MARKET_RUN_DOCS`). Tests still call `ReplayAdapter.replay`, never
`ServeLoop` (23 green tests of the parallel engine).

Calling `PaperExecutor.submit` from a private `enumerate(seq)` loop is
not composing production. Relabeling it a "synthetic policy driver" in
the module docstring, node notes, ADR Consequences, and journal A18872
does not change the object graph. A second project would not copy this;
it would use `ServeLoop`. That is still the tiering defect: the generic
tick loop lives in `dskit.production` and is re-owned in the child.

ADR-0117 **Files** still claims `replay.py` "composes `PaperExecutor` +
injected clock; it does not own clocks, ledgers, account folds." Cycle-3
did not touch the ADR. The code still constructs the clock, owns
`HorizonBook`, and injects nothing from `compose`. Consequences still
*denies* ServeLoop composition — so the ADR no longer lies that this *is*
ServeLoop — while Files still lies that clocks/folds are not owned.
Honesty about "not ServeLoop" while shipping the forbidden second engine
is the round-1 Critical, not a fix, and not remaining-work theater that
converts a FAIL into a PASS.

Phase 5 items 3–5 still cannot be expressed on this object graph.

### Major — proposed ADR-0117 still licenses the unaccepted overlap book

Kickoff: write the phase ADR and **get it accepted before implementation
continues past config-shape tests.** Plan §1: do not write implementation
code until the required ADR is accepted. ADR-0117 **Status: proposed**
(2026-09-10). Cycle-3 did not flip Status (correct as a status line; this
is not a self-accepted ADR — see "checked, not defective").

Round-2 already struck "Implementation may proceed against synthetic caps
only" and left "Synthetic tests of the fill/overlap book may run against
development-only caps." That waiver is still in Consequences. Cycle-3
then shipped more of the proposed book (halt type, unique asof, a
calendar fill-only window) while Status is proposed. That is still
implementation continuing past config-shape tests.

The new fill-only `+2 UTC midnights` rule is **not even in the ADR**.
Kickoff on overlap: propose the rule in the ADR for acceptance; "do not
silently invent it in code without it being in the ADR for acceptance."
Cycle-3 invented a tape-window rule in `_fill_only_end_ms` with zero ADR
diff. Putting halt-catch-up in the ADR as proposed and shipping it was
cycle-2's unauthorized build; shipping a bound the ADR does not name is
the same shape, quieter.

ADR-0114 item 5/6 pointers remain accurate as pointers. They do not
authorize shipping unaccepted mechanics, including this cycle's calendar
window.

### Major — overlap/expiry knobs still do not dispatch; JSON pins Python policy

Kickoff / ADR-0117 Decision (owner-ruled shape): every fill-model value is
a named JSON field; "never a Python literal a future change requires
editing code to reach."

Cycle-3 did not touch `_VOCAB`, `HorizonBook.open_lot`, or the
`else: raise ConfigError` arms. Re-proved above: flipping any overlap /
order / halt / expiry member refuses at `FillPolicy` load. The walk's
`else` arms cannot fire for any object `FillPolicy` will construct.

- `HorizonBook.open_lot` (`replay.py` 243):
  `if self._policy.same_lead_overlap == "refuse" and key in self._lots`.
  After construction the first conjunct is always true. No `override`
  branch.
- `different_lead_overlap` is still asserted **after** the per-symbol walk
  (`replay.py` 428–431). Concurrent-leads is the dict keyed by
  `(symbol, lead)` regardless.
- `halt_handling == "skip"` (`replay.py` 404) still has **no** `else:
  raise`. Cycle-3 added a bool type check on the flag; it did not
  fail-close the handling member. If `_VOCAB` ever grew, a halted bar
  would fall through into exits/entries (fail-open). JSON still cannot
  select `queue`.
- `mark_source` and `forced_exit_at`: zero attribute loads.

Numeric `fill_bar_offset` / `fill_price_field` *are* read (method lens;
not re-litigated). That does not make overlap a config interface.
Admitting in Consequences that a different ruling "changes `_VOCAB` and
the book" is honesty that the kickoff's config-only change is **not**
what shipped.

`deployment_eligible=false` and `caps=development-only` remain enforced in
`DevelopmentReplay.validate_params`. Digest pin still matches
`FillPolicy.digest()`. Those parts of the interface hold.

### Major — cycle-3 hardcoded `extra_days=2` is ungraded tape policy

New this cycle. `_exclusive_end_ms` → `_utc_day_end_ms(1)` restates the
old "midnight after `evidence_end`" conversion. `_fill_only_end_ms` →
`_utc_day_end_ms(2)` is a new computational bound: one extra UTC calendar
day of bars after the last included date.

That `2` is not in `DevelopmentReplay._PARAMS`, not in
`fill-policy.json`, not in `run-development-replay.json` params, and not
in ADR-0117 (zero-byte decision-log diff). Kickoff: fill-model values are
named JSON fields, "never a Python literal a future change requires
editing code to reach." Working agreement: never hardcode what could
change; a graded computational change belongs in identity.

Proof that it is graded in fact and ungraded in identity:

- Same document hash as cycle-1 (`fa648c6b…`). Changing `2` to `3` in
  Python would accept a different tape and **not** move the hash.
- Node notes still say "Trailing bars after evidence_end may fill
  last-day decisions" — the cycle-2 unbounded-fill description — and
  never name `+2` midnights. Notes are excluded from identity, so this
  is not a pin.
- A18872 documents the window in the journal ("evidence_end+2 UTC
  midnights"). A journal sentence is not a config field.
- The bound test refuses `2026-01-01` (far past fill_end) and allows
  `2025-10-17`. It does not pin the actual `2`-derived instant
  `2025-10-18T00:00Z`. The literal and the test timestamps are unpinned
  copies.
- UTC calendar days are not sessions. `evidence_end="2025-10-17"`
  (Friday) sets `fill_end=2025-10-19T00:00Z`; a Monday 13:30Z next-session
  bar is refused. The shipped date is Thursday, so this document luckily
  gets a Friday fill-only day; the mechanism is still `timedelta(days=2)`.

A trailing fill window that halt-catch-up, weekends, or a longer lead
would change is exactly a value that must not live as `2` in a helper.
Putting it on `DevelopmentReplay.run` also grows the private doorway that
is the Critical — the bound is not a `ReplayTape` / `ServeLoop` concern
because those objects are still unused.

## Nits

- `_HashView` is still duplicated in `replay.py` and `tests/test_replay.py`.
- Child README tree still names `replay.py` and both configs, still omits
  `nodes_capital.py`. AGENTS / CLAUDE configs-line still names
  `fill-policy` and not `development-replay` (README configs-line does
  name both). Cycle-3 did not touch the trees.
- `fills.sort` still uses `0 if row["kind"] == "exit" else 1`. Extra
  `Co-authored-by: gdrusse` trailer remains on `abdb71f`; author line
  itself is Cursor Grok 4.6.
- `cost_model` is `import_ref`'d then constructed with
  `SchwabCostModel._PARAMS` and `_VOCAB` still allows only that one class.
- Node notes in `run-development-replay.json` still describe unbounded
  trailing bars. `_halted` docstring still says "comparing by value (not
  identity)" after the field must be a JSON bool.

## Items checked, not defective under this lens

- **`dskit/production`:** zero-byte diff on cycle-3 and on the full Gate 5
  span, including README/CLAUDE. Correct given Gate 5a (no generic hook).
  Empty production diff is **not** composition; it is evidence the child
  still routes around the loop (the Critical).
- **`path.csv`:** repo-root and child, empty diff on cycle-3 and on the
  full Gate 5 span. Agents did not edit Path.
- **ADR-0117 Status:** remains `proposed`. Cycle-3 did **not** flip it to
  accepted. That specific hunt is clean; the Major is unauthorized
  implementation under a still-proposed ADR, not a status-line lie.
- **Journal A18872:** `next_id = max + 1` after A18871; unique; README
  latest-10 dropped A18862 and added A18872 (display-only slide).
- **New files this cycle:** none.
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
- **`deployment_eligible`:** refused unless JSON false. Not a
  deployment-eligible replay.
- **Author identity:** `Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai>`.

## What this lens did not review

Fill-timing arithmetic, halt/expiry edge cases, fee cents, whether
`halted: 1` now refuses, and whether duplicate `asof_ms` can still
collide fills — reviewer 1, method lens. This review used those tests
only as evidence of what engine they exercise (`ReplayAdapter.replay`,
not `ServeLoop`). 23 passing tests of the private walk are not
ServeLoop composition.

## Handoff

FAIL. Do not merge this as Gate 5 production-parity replay. Cycle-3 is a
method-shaped fail-closed patch on the same parallel engine, plus a new
ungraded UTC day count. Rebuild so equity policy (bar choice,
next-bar-open quotes, `(symbol, lead)` identity, Schwab costs) is injected
into `compose` / `ServeLoop` / `ReplayTape` / `PaperExecutor` / the
ledger; delete the private bar-walk, including halt-catch-up and the
`_utc_day_end_ms(2)` doorway bound. Keep ADR-0117 `proposed` until the
owner accepts the overlap/halt-catch-up/tape-window rules — and do not
keep shipping those rules as the only legal runtime behind one-member
`_VOCAB` and integer literals `1`/`2`. Phase 5 items 3–5 remain unbuilt
and have no honest home on the current object graph. "Architecture
ServeLoop gap unchanged" is a correct journal sentence and a FAIL.

**Verbatim verdict:** `FAIL — 1 Critical, 3 Major, 5 Nit.`
