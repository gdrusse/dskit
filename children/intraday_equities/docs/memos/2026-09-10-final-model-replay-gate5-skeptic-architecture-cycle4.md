## TL;DR

**FAIL — 0 Critical, 1 Major, 5 Nit.**

Cycle-3's Critical is closed: `DevelopmentReplay.run` still ends in
`ReplayAdapter.replay`, and that call now constructs and ticks `ServeLoop`
(1 run, 3 ticks, 2 `LegPipeline` fills). The remaining defect is that the
child still **re-owns composition**: `compose.bundles_for` is never called,
sixteen duck-typed stubs replace the production safety spine, and the
`ServeDocument` claims `fixed-interval` 1000 ms while `_TapeCadence` actually
schedules. Method/fill-timing/fee cents are out of scope (reviewer 1,
cycle-4 method FAIL).

## Skeptic review — Gate 5 development replay (architecture lens, cycle 4)

**Reviewer task:** Gate 5 stateful replay / fill-policy, reviewer 2 of 2,
re-review after cycle-4 product `a564a8a`
**Model/effort:** Cursor Grok 4.6
**Lens:** architecture / governance / tiering / OOP / file-ownership /
ServeLoop composition ONLY. Method/fill-timing/fee cents/numpy halt
correctness are out of scope — reviewer 1 already filed
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle4.md`.
**Dispatch mode:** sequential — reviewer 2 of 2, independent of the method
lens. Prior architecture FAIL:
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle3.md`.

## Scope and commits reviewed

Product `a564a8a`, journal `f9d6e05`, method memo `040bd7c` (HEAD). Base
for the Gate 5 span named in the contract: `d39e94c`. Kickoff read from
`origin/claude/dskit-merge-reviewed-branches-de0z8z:children/intraday_equities/docs/plans/2026-09-10-gate5-replay-kickoff.md`.
ADR-0117 Status line, Files, Consequences; `replay.py`;
`configs/run-development-replay.json`; `configs/fill-policy.json`; both
child trees; journal A18876. Round-3 architecture memo is the prior
verdict, not a finding to rubber-stamp.

Owner-ruled 2026-09-10 waiver (kickoff + ADR Consequences): synthetic tests
of the fill/overlap book may run with `deployment_eligible=false` while
ADR-0117 stays **proposed**, provided the ADR names the shipped
overlap/suffix/queue/override rules. Status flip by an agent would itself
be a defect. This review does not treat “still proposed while shipping the
developmental book” as a Critical.

What I tried to accept: cycle-4 claims ServeLoop composition. Runtime
proves the loop is constructed and ticked, and `LegPipeline` is on the
submit path. That closes the round-1/2/3 Critical. It does not make
`compose.py` the composition root.

## Commands run and results

```text
$ git diff --stat d39e94c..HEAD
 children/intraday_equities/AGENTS.md               |    4 +-
 children/intraday_equities/CLAUDE.md               |    4 +-
 children/intraday_equities/README.md               |    1 +
 .../intraday_equities/configs/fill-policy.json     |    4 +-
 .../configs/run-development-replay.json            |    4 +-
 .../docs/decisioning/README.md                     |    2 +-
 .../docs/decisioning/actions.csv                   |    1 +
 ...-skeptic-method-cycle4.md                       |  241 ++++
 .../intraday_equities/intraday_equities/replay.py  | 1166 ++++++++++++++++----
 children/intraday_equities/tests/test_replay.py    |  169 ++-
 docs/architecture/decision-log.md                  |   32 +-
 11 files changed, 1373 insertions(+), 255 deletions(-)

$ git diff d39e94c..HEAD -- dskit/production \
    docs/decisioning/path.csv \
    children/intraday_equities/docs/decisioning/path.csv
# empty (0 bytes) — production and both path.csv files untouched

$ PYTHONPATH=/workspace:/workspace/children/intraday_equities \
    python3 -m dskit.pipeline validate \
    children/intraday_equities/configs/run-development-replay.json \
    --adapter intraday_equities
OK — configs/run-development-replay.json
  name:  intraday-equities-development-replay
  nodes: 1  sections: —
  hash:  5adac27edf7034bc53b3eb6d515bf161aace43d2f027850016e4df0eea6fdd4e

$ PYTHONPATH=... python3 -m dskit.pipeline plan .../run-development-replay.json \
    --adapter intraday_equities
{"document_hash": "5adac27e…", "order": ["replay"],
 "nodes": {"replay": {"inputs": {}, ...}}, "edges": []}
```

Identity hash **moved** from cycle-3 `fa648c6b…` to `5adac27e…` because
`fill_suffix_bars` / `fill_suffix_weekdays` landed in `fill-policy.json`
and the digest pin in `run-development-replay.json` was updated
(`a67539e9…` → `696b1bcf…`, matches `FillPolicy.digest()`).

AST of `replay.py` at HEAD:

```text
Call ServeLoop        count=1  line 815
Call ReplayFeed       count=1  line 732
Call ReplayClock      count=1  line 731
Call ManualTime       count=1  line 730
Call PaperExecutor    count=1  line 733
Call JsonlLedger      count=1  line 758
Call HorizonBook      count=1  line 635
Call EquityReplay     count=1  line 1248
Call ReplayAdapter    count=1  line 1422
Call BarTape          count=1  line 728
Call LegPipeline      count=0  (Tick._legs constructs it)
Call bundles_for      count=0
Load compose          count=0
Load Cadence          count=0
Load PaperAccounting  count=0
Load GENESIS_HASH     count=0
digest = "a" * 64     absent
account=None          absent
for index, bar in enumerate(seq)  absent
"0" * 64              present (checkpoint head_hash, line 807)
_TapeCadence bases    ()  — not a Cadence
BarTape bases         (ReplayTape,)
EquityReplay bases    ()
DevelopmentReplay.run last:
  return ReplayAdapter(self._policy).replay(...)
ReplayAdapter.replay last:
  return EquityReplay(self._policy).run(bars, decisions)
dummy/private classes: 16
  _HashView _TapeCadence _Ready _Go _Heart _Metrics _Alerts
  _Breaker _Arming _Guards _Accounting _Lease _Reconcile
  _Verifier _AuthorityTable _PaperVenue
```

Runtime object graph (h1 buy, three bars; wraps on `ServeLoop.run`,
`Tick.run`, `LegPipeline.run`, `PaperExecutor.submit`,
`compose.bundles_for`, `ReplayClock.__init__`):

```text
ServeLoop.run            1   exit code 0
Tick.run                 3
LegPipeline.run          2   result='filled', ack='filled'
PaperExecutor.submit     2   type PaperExecutor, wrapped by _PaperVenue
ReplayClock()            1
compose.bundles_for      0
cadence type             _TapeCadence
isinstance(Cadence)      False
accounting type          _Accounting
isinstance(Accounting)   False
ManualTime shared        True (clock.time is feed._time)
SeriesState.positions    []
fills                    entry@2000 price 11.0; exit@3000 price 12.0
```

`DevelopmentReplay.run` still ends in `ReplayAdapter.replay`, which now
composes `EquityReplay.run` → `_run_loop` → `ServeLoop(...)`; `loop.run()`.
Honesty: that chain is ServeLoop composition. Fills do **not** go around
`LegPipeline`. Cycle-3 Critical closed.

## Findings

### Cycle-3 Critical — closed (not re-litigated as open)

Gate 5a: children must not subclass `ServeLoop`. They do not.
Kickoff: compose `dskit.production`; reuse `ServeLoop` + `ReplayFeed` +
replay clock + paper executor + ledger. AST `ServeLoop` call count is
**1** (was **0**). One shared `ManualTime` drives `ReplayClock` and
`ReplayFeed` (was a fresh `ReplayClock()` per symbol). Dummy
`digest = "a" * 64` and `account=None` are gone. The private
`for index, bar in enumerate(seq)` scheduler is gone; `ServeLoop._serve`
asks `_TapeCadence.next_tick`. `Tick._legs` constructs `LegPipeline`;
step (7) `act` reaches `PaperExecutor.submit` through `_PaperVenue`.
ADR-0117 Files now names that graph. Node notes no longer claim “Not
ServeLoop.”

### Major — child re-owns composition: dummy ServeDocument + 16 stubs, `compose.bundles_for` never called

Production: `compose.py` is the composition root. `ReplayTape` is DATA
(instants, feed results, id allocations) “and never an object, so which
objects a replay runs stays `compose.py`'s decision”
(`dskit/production/AGENTS.md`; `compose._TapeInjection` at
`compose.py` 405–426). Gate 5a forbids a new production hook; it does
not license a second composition root.

What ships in `EquityReplay._run_loop` (`replay.py` 725–829):

- `BarTape` is a real `ReplayTape` subclass, then used only as a bag of
  `FeedResult`s. The child copies `_TapeInjection` locally
  (`ManualTime` + `ReplayClock` + `ReplayFeed`) instead of
  `bundles_for(..., tape=tape)`. Runtime `bundles_for` count **0**.
- Sixteen duck-typed collaborators (`_Ready`, `_Go`, `_Heart`,
  `_Metrics`, `_Alerts`, `_Breaker`, `_Arming`, `_Guards`,
  `_Accounting`, `_Lease`, `_Reconcile`, `_Verifier`,
  `_AuthorityTable`, `_TapeCadence`, `_PaperVenue`, plus `_HashView`)
  stand in for production Health / Readiness / Cadence / Accounting /
  Guards / Breaker / Arming / Heartbeat / Metrics / Alerts / Lease /
  Reconcile / Verifier. Always-GO readiness, pass-through guards,
  never-due reconcile, empty `positions=()`, classify always
  `"increase"`.
- `_TapeCadence` is **not** a `Cadence` (AST bases empty;
  `isinstance(..., Cadence)` is False). `Cadence.next_tick` is an
  `@abstractmethod`. The child duck-types a parallel cadence beside
  `CADENCE_KINDS` (`OnData`, `AtTimes`, `FixedInterval` already exist).
- `_Accounting` is **not** an `Accounting`. Docstring: “Snapshot with a
  real digest so the permit digest gate passes.” `evidence_digest` is
  the fill-policy hash; `PaperAccounting` is never imported. The permit
  gate is theater.
- `_shadow_document` (`replay.py` 1087–1171) is a Python dict, not a
  hashed serve JSON. It claims
  `"cadence": {"uses": "fixed-interval", "params": {"period_ms": 1000}}`
  and `"accounting": {"uses": "paper"}` and
  `"proposer": {"uses": "intent-rows"}`. The object graph injects
  `_TapeCadence(times)`, `_Accounting`, and `EquityReplay` as the
  decider. Changing `period_ms` in that dict does **not** move pipeline
  identity `5adac27e…` and does **not** change when ticks fire.
- `_release_for` stuffs `policy.digest()` into `run_hash`,
  `serving_hash`, adapter digest, `approval_fingerprint`,
  `lease_fingerprint`, and `checklist_digest` — one fill-policy hash
  reused as every production identity slot.
- `JsonlLedger` is opened under `tempfile.mkdtemp` and deleted in
  `finally: shutil.rmtree`. ADR Consequences honestly leave Phase 5
  items 3–5 unbuilt; the Files claim of composing `JsonlLedger` is a
  scratch harness, not a durable series.

None of `_TapeCadence`, the dummy accounting digest gate, the fake
serve document, or the `bundles_for` bypass is named in ADR-0117.
Owner waiver covers shipping the named overlap/suffix/queue/override
book while proposed; it does not cover ungraded composition theater.

Kickoff still says never own clocks/ledgers/account folds. One shared
`ReplayClock` is now injected into `ServeLoop` (the old per-symbol
clock is closed). HorizonBook as `(symbol, lead)` overlay **is** named
in ADR Files this cycle — not re-raised. The dummy `_Accounting` /
`_TapeCadence` / `_shadow_document` graph is the unauthorized remainder.

A second project would not copy sixteen stubs; it would hand a
`ReplayTape` to `compose.bundles_for`. That is still the tiering
defect, now at the composition root rather than at the scheduler.

## Nits

- `EquityReplay` is a public-looking class (no `_` prefix) and is **not**
  in `__all__` (`BarTape`, `DevelopmentReplay`, `FillPolicy`,
  `HorizonBook`, `ReplayAdapter` only). No underscore name leaks into
  `__all__`.
- `_HashView` is still duplicated in `replay.py` and
  `tests/test_replay.py`.
- Checkpoint `head_hash="0" * 64` restates `GENESIS_HASH` without
  importing it (`dskit.production.base.GENESIS_HASH`).
- Extra `Co-authored-by: gdrusse` trailer remains on `a564a8a` and
  `f9d6e05`; author line itself is Cursor Grok 4.6.
- `test_replay_py_composes_serveloop_replayfeed_tape_and_shared_clock`
  AST-pins `ServeLoop` / `ReplayFeed` / `ReplayClock` / `ReplayTape` and
  the absence of `enumerate(seq)` / `"a"*64`. It does not pin
  `LegPipeline` or a zero `bundles_for` (or a required non-zero). The
  composition root gap is untested.

## Items checked, not defective under this lens

- **Cycle-3 Critical (private bar-walk scheduler, ServeLoop call count 0):**
  closed. Proof above. `DevelopmentReplay.run` → `ReplayAdapter.replay`
  → `EquityReplay.run` → `ServeLoop.run`. Fills go through
  `LegPipeline` (`result='filled'`, two `PaperExecutor.submit` calls).
- **`dskit/production`:** zero-byte diff on `d39e94c..HEAD`. Correct
  given Gate 5a (no generic hook). Empty production diff is no longer
  evidence of routing around the loop; it is now evidence they
  hand-assembled the loop instead of calling `compose.bundles_for`.
- **`path.csv`:** repo-root and child, empty diff. Agents did not edit
  Path.
- **ADR-0117 Status:** remains `proposed (2026-09-10)`. Cycle-4 edited
  Files / closed vocabularies / fill-suffix / Consequences waiver text
  and did **not** flip Status to accepted. That hunt is clean.
- **Named overlap/suffix/queue/override while proposed:**
  `_VOCAB` now has `halt_handling=skip|queue` and
  `same_lead_overlap=refuse|override` with `else: raise` dispatch;
  `fill_suffix_bars=1171` / `fill_suffix_weekdays=5` are JSON fields;
  both are in the ADR. `deployment_eligible` still refused unless JSON
  false. Caps still `development-only`. Waiver applies; not a Critical.
- **One-member remaining vocabs** (`order_type`, `rejections`,
  `forced_exit_at`, `forced_exit_horizon_basis`, `mark_source`,
  `different_lead_overlap`, `same_tick_order`, `paper_fill_rule`,
  `paper_fees`, `cost_model`): JSON cannot select another member, but
  `evaluate` / `open_lot` now load `mark_source`, `forced_exit_at`,
  `different_lead_overlap` and fail-close. ADR names those closed sets.
  Not re-raised as “cannot dispatch.”
- **Cycle-3 ungraded `extra_days=2`:** closed. Suffix is a fill-policy
  field; changing it moves fill-policy identity and the pipeline pin
  (`fa648c6b…` → `5adac27e…`).
- **HorizonBook overlay:** named in ADR Files this cycle as the equity
  `(symbol, lead)` identity production positions do not key.
  `SeriesState.positions` stays empty by that proposed split. Not a
  separate finding under the waiver.
- **Trees:** README names `replay.py` and `nodes_capital.py`. AGENTS /
  CLAUDE configs-line names `fill-policy` and `development-replay`.
  Cycle-3 tree nit closed. No new package files this cycle beyond the
  method memo (this architecture memo is the only file this reviewer
  may write).
- **Journal A18876:** `next_id = max + 1` after A18875; unique; README
  latest-10 slid.
- **No `__all__` leak of `_` names.** Child does not subclass
  `ServeLoop`. SchwabCostModel still extracted in the child and
  imported. No `dskit.production` hook.
- **§11 items 4, 7, 8, 9, 10:** not silently ruled.

## What this lens did not review

Fill-timing arithmetic, `forced_exit_price_field` vs recorded exit
price, swallowed `KeyError`/`TypeError`, override/queue fill-drop
behavior, halt/expiry edge cases, fee cents — reviewer 1, method lens.
This review used those tests only as evidence of what engine they
exercise (`ServeLoop` + `LegPipeline`, not a private `enumerate`
walk). Passing method tests of a dummy collaborator graph are not
`compose.bundles_for`.

## Handoff

FAIL. Do not merge this as Gate 5 production-parity replay. Cycle-4
did compose and tick `ServeLoop`, and fills do go through
`LegPipeline` — the round-1/2/3 Critical is closed. Rebuild
composition so a `ReplayTape` is handed to `compose.bundles_for`
(or an owner-accepted equivalent that does not invent sixteen stubs
and a lying `ServeDocument`). Keep ADR-0117 `proposed` until the owner
accepts overlap/suffix/queue/override **and** whichever composition
rule replaces the dummy graph. Phase 5 items 3–5 remain unbuilt and
still have no durable ledger once `rmtree` runs.

**Verbatim verdict:** `FAIL — 0 Critical, 1 Major, 5 Nit.`
