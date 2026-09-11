## Skeptic review — Gate 5 development replay (architecture lens)

**Reviewer task:** Gate 5 stateful replay / fill-policy, reviewer 2 of 2
**Model/effort:** Cursor Grok 4.6
**Lens:** architecture / governance / tiering / OOP / file-ownership ONLY.
Whether individual fill-timing unit tests can fail is reviewer 1's method
lens. This review does not re-litigate that. It hunts parallel engines,
ServeLoop composition lies, ADR self-authorization, config-as-interface
theater, tiering, trees, path.csv, and §11 inference.
**Dispatch mode:** sequential — reviewer 2 of 2, independent of the method
lens, over `c286897` vs `origin/claude/phase1-recovery-seven-gates-ao4zdj`.

## TL;DR

**FAIL — 1 Critical, 2 Major, 3 Nit.**

`ReplayAdapter.replay` is `backtest.py` under another name. Gate 5a proved
`ServeLoop` already drives deterministic historical ticks and forbade a new
production hook; this commit then built a private bar-walk beside that loop,
constructed a `TestClock`, folded lots in `HorizonBook`, and called
`PaperExecutor.submit` with a dummy digest and `account=None`. ADR-0117 stays
`proposed` (not self-accepted as Status), but its Consequences authorize the
build the kickoff forbade past config-shape tests, and the overlap rule is
locked in Python while JSON only default-denies other members.

## Scope and commits reviewed

Base `origin/claude/phase1-recovery-seven-gates-ao4zdj` through `HEAD`
(`c286897`). One commit:

```text
c286897 Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai>
  feat(gate5): development replay and config-driven fill-policy
```

Author is the exact model (not a family name). Kickoff read from
`origin/claude/dskit-merge-reviewed-branches-de0z8z:children/intraday_equities/docs/plans/2026-09-10-gate5-replay-kickoff.md`.
Plan §3 / §6 Phase 5 / §8 / §9 lens 7, ADR-0114 Phase 5, ADR-0117, Gate 5a
architecture memo, `replay.py`, `nodes_capital.py`, both new configs, tests,
child trees, journal row A18868.

## Commands run and results

```text
$ git diff origin/claude/phase1-recovery-seven-gates-ao4zdj...HEAD --stat
 children/intraday_equities/AGENTS.md               |  10 +-
 children/intraday_equities/CLAUDE.md               |  10 +-
 children/intraday_equities/README.md               |   3 +-
 .../configs/fill-policy.json                       |  34 ++
 .../configs/run-development-replay.json            |  17 +
 .../docs/decisioning/README.md                     |   2 +-
 .../docs/decisioning/actions.csv                   |   1 +
 .../intraday_equities/__init__.py                  |   4 +
 .../intraday_equities/nodes_capital.py             |  93 +++-
 .../intraday_equities/replay.py                    | 618 +++++++++++++++++++++
 .../tests/test_configs.py                          |  33 +-
 .../tests/test_replay.py                           | 364 ++++++++++++
 docs/architecture/decision-log.md                  |  77 ++-
 13 files changed, 1235 insertions(+), 31 deletions(-)

$ git diff origin/claude/phase1-recovery-seven-gates-ao4zdj...HEAD \
    -- dskit/production docs/decisioning/path.csv \
       children/intraday_equities/docs/decisioning/path.csv
# empty — production and both path.csv files untouched

$ git log -1 --format='%an <%ae>'
Cursor Grok 4.6 <cursor-grok-4.6@opencode.ai>

$ python3 -m pytest tests/test_replay.py -q --tb=line
# from children/intraday_equities; 16 passed in 0.24s

$ PYTHONPATH=. python3 -m dskit.pipeline validate configs/run-development-replay.json --adapter intraday_equities
OK — configs/run-development-replay.json
  name:  intraday-equities-development-replay
  nodes: 1  sections: —
  hash:  fa648c6b04b289f8533337312caf29efcc33aee3e78fdf709b03b3fbb39411f4

$ PYTHONPATH=. python3 -m dskit.pipeline plan configs/run-development-replay.json --adapter intraday_equities
{"document_hash": "fa648c6b…", "order": ["replay"],
 "nodes": {"replay": {"inputs": {}, ...}}, "edges": []}
```

AST of `replay.py` imports: `dskit.production.clock`, `.executor`,
`.records`, `.state`. Not `loop`, `feed`, `compose`, `ledger`, `accounting`,
`bundles`. `ServeLoop` / `ReplayFeed` / `ReplayClock` appear only in the
module docstring.

## Findings

### Critical — `ReplayAdapter` is a parallel backtest engine

ADR-0114 Phase 5 and plan §5: reuse `ServeLoop` + `ReplayFeed` + replay
clock + paper executor + ledger; do not create `backtest.py` as a parallel
engine. Gate 5a (architecture PASS): no missing generic hook; children must
not subclass `ServeLoop`. Kickoff: subclass or compose `dskit.production`,
never own clocks / ledgers / account folds / generic performance math.
Production `loop.py`: `ServeLoop` is the scheduler; `compose.py` chooses
objects; `ReplayTape` is the replay seam (data, never a private loop).
Production `clock.py`: `TestClock` is "a hand-driven clock for tests";
replay shares `ManualTime` via `ReplayClock`.

What shipped:

- `DevelopmentReplay.run` calls `ReplayAdapter(self._policy).replay(...)`.
  No `ServeLoop`, `ReplayTape`, `ReplayFeed`, `ReplayClock`, `LegPipeline`,
  or ledger.
- `_replay_symbol` constructs `TestClock(start_ms=...)`, `PaperExecutor`,
  and `HorizonBook`, then walks bars itself (`replay.py` ~343–361).
- `digest = "a" * 64` and `TickState(..., account=None, ...)`
  (`replay.py` ~346, ~459–461). Submission skips `LegPipeline`.
- `HorizonBook._lots` is a second position fold keyed by `(symbol, lead)`.
  Production already owns `state.PositionBook` / `SeriesState` as the sole
  ledger fold. This book is the replay's account; it is never applied to
  `SeriesState`.
- Fees: `paper_fees=none`, then Schwab floats are attached on a sidecar
  dict. `PaperExecutor` is used as a touch-fill price oracle, not as the
  venue in a serve tick.
- `configs/run-development-replay.json` notes claim "Same decision-graph
  contract as production (one graph, one account) with only the
  fill/clock/feed boundary replaced." `plan` of that document: **one** node,
  `inputs: {}`, `edges: []`. Plan §3's graph (features → bundle → caps →
  MIO → executor → ledger) is absent. `test_configs.py` even exempts this
  file from market-run graph pins (`_NON_MARKET_RUN_DOCS`).

Calling `PaperExecutor.submit` from a private `for index, bar in
enumerate(seq)` loop is not composing production. It is the parallel engine
ADR-0114 forbade, wearing the Gate 5a sentence "we did not subclass
`ServeLoop`" as camouflage. A second project would not copy this; it would
use `ServeLoop`. That is also the tiering defect: the generic tick loop
already lives in `dskit.production` and was re-owned in the child.

Phase 5 items 3–5 (crash/restart, solvency after every fill,
rejected/unfunded observability) cannot be expressed on this object graph.
The ADR does not claim those items shipped; the config's "one account" /
"accounting defects" language still describes an artifact that has no
account.

### Major — proposed ADR-0117 authorizes its own implementation

Kickoff: write the phase ADR and **get it accepted before implementation
continues past config-shape tests.** Plan §1: do not write implementation
code until the required ADR is accepted. ADR-0117 **Status: proposed**
(2026-09-10), "Does **not** self-accept," overlap/expiry "proposed, not
ruled." Same entry **Consequences:** "Implementation may proceed against
synthetic caps only."

This commit is not config-shape tests. It ships `ReplayAdapter.replay`,
`HorizonBook`, 16 passing mechanism tests, and closed vocabularies that
refuse any overlap member other than the proposed rule. Status was not
flipped to `accepted` (not the hunt's Critical "self-accepted ADR"), but
the proposed text grants the waiver the kickoff withheld. Overlap is de
facto locked in shipped code while the owner has not accepted the ADR.

ADR-0114 item 5/6 pointers ("Owner ruled mixed caps / next-bar-open;
overlap/fill-policy proposed in ADR-0117 (not accepted)") are accurate as
pointers. They do not authorize shipping the unaccepted mechanics.

### Major — overlap/expiry is Python policy, not a live config interface

Kickoff / ADR-0117: every fill-model value is a named JSON field; "a
different ruling is a config-vocabulary change, not a silent code default."

`FillPolicy._VOCAB` default-denies unknown members (correct deny). After
load, `HorizonBook` never reads `same_lead_overlap`,
`different_lead_overlap`, `same_tick_order`, or
`forced_exit_horizon_basis`. AST Load of those names in `replay.py` is
empty except `paper_fees` (copied into `PaperExecutor` knobs).
`HorizonBook.open_lot` hardcodes refuse-if-open
(`replay.py` ~236–247). Expiry is hardcoded `fill_index + lead` (~245).
`_replay_symbol` always runs exits then entries (~349–361).

Changing JSON to `same_lead_overlap: "override"` raises `ConfigError`; it
does not select a different rule. An owner ruling other than the proposed
members requires a code edit of `_VOCAB` **and** `HorizonBook` /
`_replay_symbol`. That is a hardcoded policy with a JSON pin, not config
as the interface. Numeric `fill_bar_offset` *is* read (the one knob that
meets the kickoff). Closed one-member vocab would be honest default-deny
if the ADR did not claim a later ruling is a config-only change.

`deployment_eligible=false` and `caps=development-only` are actually
enforced in `DevelopmentReplay.validate_params`. Digest pin
`fill_policy_sha256` matches `FillPolicy.digest()` (notes stripped via
`config_hash` / `_strip_notes`). Those parts of the interface hold.

## Nits

- `_HashView` is duplicated in `replay.py` and `tests/test_replay.py`
  (second copy of a tiny `config_hash` adapter).
- Child README tree added `replay.py` and names both new configs; AGENTS /
  CLAUDE configs line names `fill-policy` but not `development-replay`.
  The edited README tree still omits `nodes_capital.py` (this commit
  modified that file).
- `fills.sort` uses `0 if row["kind"] == "exit" else 1` (`if kind==` in
  the private engine). Extra `Co-authored-by: gdrusse` trailer on the
  commit; author line itself is Cursor Grok 4.6.

## Items checked, not defective under this lens

- **`dskit/production`:** zero-byte diff, including README/CLAUDE. Correct
  given Gate 5a (no generic hook).
- **`path.csv`:** repo-root and child, empty diff. Journal A18868 is
  `next_id = max+1` after A18867; README latest-10 slide only.
- **ADR-0117 Status:** remains `proposed`. Not flipped to accepted.
- **SchwabCostModel** extracted in the child and imported by `replay.py`
  (not copied). Equity-domain cost stays in the child. Correct tiering
  for that formula.
- **`fill-policy.json`:** kickoff allowed "extend `capital-policy.json` or
  add a sibling"; ADR-0117 picks the sibling because Phase 3's
  `capital-policy.json` does not exist. Authorized, not an unrequested
  file. `replay.py`, `run-development-replay.json`, `test_replay.py` were
  kickoff/plan-named.
- **No `__all__` leak of `_` names.** No `NotImplementedError` seam.
  Child production imports (`TestClock`, `PaperExecutor`, `Intent`,
  `Quote`, `SimulatedPermit`, `TickState`) are public `__all__` names —
  used to route around `ServeLoop`, which is the Critical, not a private
  import.
- **§11 items 4, 7, 8, 9, 10:** not silently ruled. Caps forced
  `development-only`; Schwab rates in JSON are labeled illustrative;
  monitors / MIO remaining knobs / reporting-alert numerics untouched.
  Item 5/6 *shape* is owner-ruled in the kickoff; precise overlap is the
  proposed ADR (see Majors), not a quiet extra §11 close.
- **`deployment_eligible`:** refused unless JSON false. Not a
  deployment-eligible replay.

## What this lens did not review

Fill-timing arithmetic, halt/expiry edge cases, fee cents, and whether
each unit test can fail — reviewer 1, method lens. This review used those
tests only as evidence of what engine they exercise (`ReplayAdapter.replay`,
not `ServeLoop`).

## Handoff

FAIL. Do not merge this as Gate 5 production-parity replay. Do not
self-accept ADR-0117. Rebuild so equity policy (bar choice, next-bar-open
quotes, `(symbol, lead)` identity, Schwab costs) is injected into
`compose` / `ServeLoop` / `ReplayTape` / `PaperExecutor` / the ledger;
delete the private bar-walk. Keep ADR-0117 `proposed` until the owner
accepts the overlap rule — or stop shipping that rule as the only legal
runtime. Phase 5 items 3–5 remain unbuilt and have no honest home on the
current object graph.

**Verbatim verdict:** `FAIL — 1 Critical, 2 Major, 3 Nit.`
