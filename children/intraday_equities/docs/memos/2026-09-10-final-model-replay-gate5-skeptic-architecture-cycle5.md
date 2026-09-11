## TL;DR

**PASS — 0 Critical, 0 Major, 8 Nit.**

Cycle-4's composition Major is closed: `EquityReplay._run_loop` calls
`compose.bundles_for(..., tape=tape)` without `clock=`; `_TapeCadence` is a
`Cadence`; the thirteen safety stubs are gone; `ServeLoop` ticks compose's
safety/lease/health/ledger/`PaperAccounting`. The ServeDocument still
claims `fixed-interval` 1000 ms while tape times fire (graded Nit: named
overlay, `CADENCE_KINDS` has no tape-times member). Method FAIL is out of
scope (reviewer 1, cycle-5).

## Skeptic review — Gate 5 development replay (architecture lens, cycle 5)

**Reviewer task:** Gate 5 stateful replay / fill-policy, reviewer 2 of 2,
re-review after cycle-5 product `673377e`
**Model/effort:** Cursor Grok 4.6
**Lens:** architecture / governance / tiering / OOP / file-ownership /
ServeLoop composition ONLY. Method/fill-timing/fee cents/numpy halt /
`InvalidOperation` vs `_fault` are out of scope — reviewer 1 already
filed
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle5.md`
(90ea3ce; FAIL 0C 1M 3m).
**Dispatch mode:** sequential — reviewer 2 of 2, independent of the method
lens. Prior architecture FAIL:
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle4.md`
(47e529f).

## Scope and commits reviewed

Product `673377e3a087dfc502107e9c81409b3d3a6a631f`, journal
`5f49bfa2843d0970b60a6e1a15c51ae5f71ddf9c` (A18877), method memo `90ea3ce`
(HEAD). Base for the Gate 5 span: `d39e94c`. Kickoff read from
`origin/claude/dskit-merge-reviewed-branches-de0z8z:children/intraday_equities/docs/plans/2026-09-10-gate5-replay-kickoff.md`.
ADR-0117 Status / Files / Consequences at `673377e`; `replay.py`;
`configs/run-development-replay.json`; both child trees; journal A18877.
Cycle-4 architecture memo is the prior verdict to re-prove, not a finding
to rubber-stamp.

Owner-ruled 2026-09-10 waiver (kickoff + ADR Consequences): synthetic tests
of the named overlap/suffix/queue/override book may run with
`deployment_eligible=false` while ADR-0117 stays **proposed**. Status flip
by an agent would itself be a defect. Ungraded composition theater is not
covered by that waiver. A named overlay required because `CADENCE_KINDS`
has no tape-times member (Gate 5a forbids adding one) is Nit rather than
Major when compose is actually called, `Cadence` is subclassed, the
sixteen stubs are gone, and the safety spine is compose's.

What I tried to break: cycle-5 claims `bundles_for(tape=tape)` plus
overlays. Passing `test_replay.py` is not that proof. AST + wraps on
`ServeLoop.run` / `Tick.run` / `LegPipeline.run` / `PaperExecutor.submit`
/ `compose.bundles_for` / `ReplayClock.__init__` plus a `period_ms=5000`
monkeypatch are.

## Commands run and results

```text
$ git diff --stat d39e94c..HEAD
 children/intraday_equities/AGENTS.md               |    4 +-
 children/intraday_equities/CLAUDE.md               |    4 +-
 children/intraday_equities/README.md               |    1 +
 .../intraday_equities/configs/fill-policy.json     |    4 +-
 .../configs/run-development-replay.json            |    4 +-
 .../intraday_equities/docs/decisioning/README.md   |    4 +-
 .../intraday_equities/docs/decisioning/actions.csv |    2 +
 ...-skeptic-architecture-cycle4.md                 |  319 ++++++
 ...-skeptic-method-cycle4.md                       |  241 +++++
 ...-skeptic-method-cycle5.md                       |  227 ++++
 .../intraday_equities/intraday_equities/replay.py  | 1080 ++++++++++++++++----
 children/intraday_equities/tests/test_replay.py    |  221 +++-
 docs/architecture/decision-log.md                  |   41 +-
 13 files changed, 1899 insertions(+), 253 deletions(-)

$ git diff d39e94c..HEAD -- dskit/production \
    docs/decisioning/path.csv \
    children/intraday_equities/docs/decisioning/path.csv | wc -c
0

$ PYTHONPATH=/workspace:/workspace/children/intraday_equities \
    python3 -m dskit.pipeline validate \
    children/intraday_equities/configs/run-development-replay.json \
    --adapter intraday_equities
OK — children/intraday_equities/configs/run-development-replay.json
  name:  intraday-equities-development-replay
  nodes: 1  sections: —
  hash:  5adac27edf7034bc53b3eb6d515bf161aace43d2f027850016e4df0eea6fdd4e

$ PYTHONPATH=... python3 -m dskit.pipeline plan .../run-development-replay.json \
    --adapter intraday_equities
{"document_hash": "5adac27e…", "order": ["replay"],
 "nodes": {"replay": {"uses": "intraday_equities.replay:DevelopmentReplay", ...}},
 "edges": []}
```

Identity hash **unchanged** from cycle-4 `5adac27e…` (fill-policy digest pin
still `696b1bcf…`). Product `673377e` touches `replay.py`, `test_replay.py`,
ADR-0117 Files only (Status stays proposed). Journal `5f49bfa` is A18877.
Method memo `90ea3ce` is docs-only.

AST of `replay.py` at HEAD (`/tmp/gate5_arch_c5_ast.py`):

```text
Call ServeLoop        count=1  line 640
Call bundles_for      count=1  line 612
Call PaperExecutor    count=1  line 627
Call ReleaseIdSource  count=1  line 638
Call BarTape          count=1  line 590
Call HorizonBook      count=1  line 497
Call EquityReplay     count=1  line 1172
Call ReplayAdapter    count=1  line 1346
Call _TapeCadence     count=1  line 634
Call Data             count=1  line 635
Call LegPipeline      count=0  (Tick._legs constructs it)
Call ReplayFeed       count=0  (compose._TapeInjection)
Call ReplayClock      count=0  (compose._TapeInjection)
Call ManualTime       count=0  (compose._TapeInjection)
Call JsonlLedger      count=0  (compose ledger_class)
Call PaperAccounting  count=0  (compose accounting.uses=paper)
Call RecordedIdSource count=0  (compose tape injection; overlaid away)
Call GENESIS_HASH     count=0
Call FixedInterval    count=0  (compose builds it, child never names it)
Load compose          from dskit.production.compose import bundles_for
Load Cadence          from dskit.production.cadence import Cadence
digest = "a" * 64     absent
account=None          absent
for index, bar in enumerate(seq)  absent
"0" * 64              absent
_TapeCadence bases    ('Cadence',)
BarTape bases         ('ReplayTape',)
EquityReplay bases    ()
DevelopmentReplay bases ('Node',)
bundles_for kwargs    serve_root, secrets, invocation, process_id,
                      lock, journal_hook, tape=tape   (no clock=)
DevelopmentReplay.run last:
  return ReplayAdapter(self._policy).replay(...)
ReplayAdapter.replay last:
  return EquityReplay(self._policy).run(bars, decisions)
dummy/private classes: 5
  _HashView _TapeCadence _TapeEntry _TapeHead _PaperVenue
ABSENT vs cycle-4's 16:
  _Ready _Go _Heart _Metrics _Alerts _Breaker _Arming _Guards
  _Accounting _Lease _Reconcile _Verifier _AuthorityTable
```

Runtime object graph (h1 buy, three bars at 1000/2000/3000 ms; wraps on
`ServeLoop.run`, `Tick.run`, `LegPipeline.run`, `PaperExecutor.submit`,
`ShadowExecutor.submit`, `compose.bundles_for`, `ReplayClock.__init__`,
`FixedInterval.next_tick`, `_TapeCadence.next_tick`, `_TapeEntry.run`,
`_TapeHead.run`, `Decider.prepare`/`read_entry`/`evaluate`,
`Readiness.evaluate`):

```text
ServeLoop.run              1   exit code 0
Tick.run                   3   at_ms [1000, 2000, 3000]
LegPipeline.run            2   result='filled', ack='filled'
PaperExecutor.submit       2   inner of _PaperVenue
ShadowExecutor.submit      0
ReplayClock()              1
compose.bundles_for        1   tape=BarTape, clock kw absent
FixedInterval.next_tick    0
_TapeCadence.next_tick     4
_TapeEntry.run             0
_TapeHead.run              0
Decider.prepare            1   (during bundles_for)
Decider.read_entry         0
Decider.evaluate           0
Readiness.evaluate         0
cadence type               _TapeCadence
isinstance(Cadence)        True
compose cadence type       FixedInterval  (then overlaid)
same_cadence               False
accounting type            PaperAccounting
isinstance(Accounting)     True
same_accounting            True
ManualTime shared          True (clock.time is feed._time)
executor compose           ShadowExecutor
executor loop              _PaperVenue(PaperExecutor)
same_executor              False
decider compose            Decider
decider loop               EquityReplay
same_decider               False
id_source compose          RecordedIdSource
id_source loop             ReleaseIdSource
same_id_source             False
same_safety                True
same_health                True
same_lease                 True  (ProcessLease)
same_ledger                True
same_readiness             True  (production Readiness)
same_feed                  True  (ReplayFeed)
same_clock                 True  (ReplayClock)
same_decision              True
same_observability         True
verifier._executor         ShadowExecutor  (compose's)
breaker._executor          ShadowExecutor  (compose's)
source_tokens shadow/paper (None, None) ==
snapshot.positions         ()
doc cadence.uses           fixed-interval  period_ms=1000
doc rung                   shadow
doc execution.uses         shadow
doc proposer.uses          intent-rows
CADENCE_KINDS              aligned-bar, at-times, fixed-interval, on-data
                           (no tape-times)
fills                      entry@2000 price 11.0; exit@3000 price 12.0
period_ms patched to 5000  ticks still [1000, 2000, 3000]; ticks_moved False
                           FixedInterval.next_tick still 0
EquityReplay subclass Loop False
DevelopmentReplay subclass False
```

`DevelopmentReplay.run` still ends in `ReplayAdapter.replay` →
`EquityReplay.run` → `_run_loop` → `bundles_for` → overlays →
`ServeLoop(...)`; `loop.run()`. Fills go through `LegPipeline`. Cycle-4
Major ingredients (compose never called / sixteen stubs / non-`Cadence`
scheduler) are closed.

## Cycle-4 Major re-test

| Cycle-4 (`a564a8a` / 47e529f) | Cycle-5 (`673377e`) |
|---|---|
| `bundles_for` AST+runtime count **0** | **Closed.** AST 1, runtime 1, kwargs include `tape=tape`, no `clock=` |
| Sixteen duck-typed stubs (`_Ready`…`_AuthorityTable`) | **Closed.** Those thirteen class defs absent; remaining `_HashView` / `_TapeCadence` / `_TapeEntry` / `_TapeHead` / `_PaperVenue` |
| `_TapeCadence` bases `()`; `isinstance(Cadence)` False | **Closed.** bases `('Cadence',)`; isinstance True |
| Dummy `digest="a"*64` / `account=None` / `enumerate(seq)` scheduler | **Closed.** All three AST-absent (`enumerate(seq)` remains only in `_index_of`) |
| Dummy ServeDocument `fixed-interval` 1000 ms while tape schedules | **Still true; graded Nit** (named overlay; see Nit 1). `period_ms=5000` does not move ticks |
| Compose result unused | **Closed as Major.** Safety/decision/observability/feed/clock/lease/ledger/accounting **are** the compose objects (`same_*=True`). Cadence/decider/executor/id_source overlaid — ADR-0117 Files names all four |
| `_Accounting` not `Accounting` | **Closed.** `PaperAccounting`, isinstance `Accounting` True, same object as compose |
| Child re-owns clocks/injection | **Closed.** `ReplayClock`/`ReplayFeed`/`ManualTime` Call count 0 in the child; compose `_TapeInjection` builds them; ManualTime shared |

## Findings

### Cycle-4 Major — closed (not re-litigated as open)

Gate 5a: children must not subclass `ServeLoop`. They do not
(`EquityReplay` MRO is `(EquityReplay, object)`; no `class …(ServeLoop)`
in the child). Kickoff: compose `dskit.production`; reuse `ServeLoop` +
`ReplayFeed` + replay clock + paper executor + ledger. Runtime
`bundles_for` count is **1** with `tape=BarTape` (was **0**). Compose's
`_TapeInjection` supplies one shared `ManualTime` to `ReplayClock` and
`ReplayFeed`. The thirteen always-GO stubs are gone; `ServeLoop` holds
compose's `Safety` / `Health` / `ProcessLease` / ledger / `Readiness` /
`PaperAccounting` / `Decision` / `Observability` (identity-equal to the
tuple `bundles_for` returned). `_TapeCadence` is a `Cadence`.
`PaperExecutor` is constructed on **compose's clock** and wrapped as
`_PaperVenue`; `LegPipeline.run` 2, `PaperExecutor.submit` 2,
`ShadowExecutor.submit` 0. ADR-0117 Files at `673377e` names
`bundles_for(..., tape=BarTape)`, the four overlays, shadow-vs-paper
(`fsync: none` is shadow-only), `ReleaseIdSource` because
`BarTape.id_allocations` is empty, `HorizonBook` as `(symbol, lead)`,
no `ServeLoop` subclass, no production hook, `path.csv` untouched.
Status line remains `proposed (2026-09-10)`.

Overlays throw away compose's `FixedInterval`, `Decider`,
`ShadowExecutor`, and `RecordedIdSource`. That is the **ADR-named equity
seam**, not a remaining Major: Files lists those four swaps and the
reason (no tape-times member; this is not a recorded series; shadow
executor is not the fill venue). Compose is the composition root; the
child does not assemble a second safety spine.

## Nits

1. **ServeDocument still lies about cadence (and proposer/executor).**
   `schedule.cadence` is `fixed-interval` / `period_ms=1000`; ticks fire
   at tape times `[1000, 2000, 3000]`. Monkeypatching that dict to
   `period_ms=5000` leaves ticks `[1000, 2000, 3000]`
   (`ticks_moved False`); `FixedInterval.next_tick` stays 0;
   `_TapeCadence.next_tick` is 4. `proposer.uses=intent-rows` and
   `execution.uses=shadow` are likewise unused after overlay. Pipeline
   identity `5adac27e…` does not move if that Python dict changes.
   Graded Nit not Major: compose is called, `Cadence` is subclassed,
   stubs are gone, safety spine is compose's, and Files names the
   cadence overlay because `CADENCE_KINDS.kinds()` is
   `aligned-bar, at-times, fixed-interval, on-data` (no tape-times;
   `at-times` is session/`HH:MM`, not epoch-ms). Residual honesty:
   `CADENCE_KINDS.resolve("intraday_equities.replay:_TapeCadence")`
   **succeeds** (child path, Gate 5a forbids a *registry member* not a
   class ref). Current `__init__(self, times)` then
   `tuple({"times": [...]})` → `('times',)`, so they overlay instead of
   shaping `_PARAMS` and putting `uses` in the document `_serve_document`
   already receives `times` for. Named overlay; dummy `uses` is a
   choice.

2. **One fill-policy digest reused as every release identity slot.**
   `run_hash == serving_hash == adapter.digest == approval_fingerprint
   == lease_fingerprint == checklist_digest == policy.digest()` =
   `696b1bcf…`. `Readiness.evaluate` count 0; no `readiness.json` exists
   in the repo; D24's checklist pin is unexercised theater on this
   unarmed path.

3. **`_TapeEntry` / `_TapeHead` never tick.** `Decider.prepare` 1 during
   `bundles_for`; both `run` counts 0; compose `Decider.read_entry` /
   `evaluate` 0 after `Data(feed=data.feed, decider=self)`. Prepare-only
   graph so compose will classify a serving run, then the equity overlay
   throws that `Decider` away. Files does not name these nodes.

4. **Verifier/breaker still hold compose's `ShadowExecutor`.**
   `safety.submission_verifier._executor is compose.execution.executor`
   (True); loop `execution.executor` is `_PaperVenue`. Submit path is
   Paper (`PaperExecutor.submit` 2, `ShadowExecutor.submit` 0).
   `source_tokens` on both is `(None, None)`, so the split does not
   refuse. Files names replacing the fill venue, not rebinding the
   verifier.

5. **`EquityReplay` is a public-looking class and is not in `__all__`**
   (`BarTape`, `DevelopmentReplay`, `FillPolicy`, `HorizonBook`,
   `ReplayAdapter` only). Package `__init__.py` also omits it. No
   underscore name leaks into `__all__`.

6. **`_HashView` is still duplicated** in `replay.py` and
   `tests/test_replay.py`.

7. **Extra `Co-authored-by: gdrusse` trailer** remains on `673377e`,
   `5f49bfa`, and `90ea3ce`; author line itself is Cursor Grok 4.6.

8. **`test_replay_py_composes_serveloop_replayfeed_tape_and_shared_clock`**
   now AST-pins `bundles_for`, `Cadence` in bases, `tape=tape`, and
   absence of `class _Ready` / `class _Accounting` / `enumerate(seq)` /
   `"a"*64`. It does not pin dummy-hex collapse, `period_ms`
   independence, `FixedInterval.next_tick==0`, or verifier-vs-submit
   executor identity.

## Items checked, not defective under this lens

- **Cycle-4 Major (child re-owns composition):** closed. Proof above.
  `DevelopmentReplay.run` → `ReplayAdapter.replay` → `EquityReplay.run`
  → `bundles_for` → `ServeLoop.run`. Fills go through `LegPipeline`.
- **`dskit/production`:** zero-byte diff on `d39e94c..HEAD`. Gate 5a (no
  generic hook). Correct.
- **`path.csv`:** repo-root and child, 0 bytes diff. Agents did not edit
  Path.
- **ADR-0117 Status:** remains `proposed (2026-09-10)` at product and
  HEAD. Cycle-5 edited Files (compose + named overlays) and did **not**
  flip Status to accepted.
- **Files vs runtime:** Files names `bundles_for(..., tape=BarTape)`,
  cadence/`Cadence` overlay, equity decider, `ReleaseIdSource`,
  `PaperVenue` around `PaperExecutor` on compose's clock, `rung=shadow`
  because `fsync: none` is shadow-only, `LegPipeline`, no `ServeLoop`
  subclass, no production hook, `HorizonBook` as `(symbol, lead)`.
  Runtime matches those sentences. `snapshot.positions` is `()` by that
  named split.
- **Named overlap/suffix/queue/override while proposed:** still in
  `_VOCAB` / JSON / ADR. `deployment_eligible` still refused unless JSON
  false. Waiver applies; not a Critical. Ungraded composition theater
  was the cycle-4 Major; it is closed.
- **Phase 5 items 3–5 / scratch `mkdtemp`+`rmtree` ledger:** AST
  `mkdtemp` 1 / `rmtree` 1. ADR Consequences already say crash/restart
  ledger identity, post-fill solvency, and live-graph unfunded
  candidates remain unbuilt. Not re-raised as Critical.
- **`GENESIS_HASH` / `"0"*64`:** absent from `replay.py` (compose's
  `ledger.head()` supplies the checkpoint). Cycle-4 nit closed.
- **paper rung vs shadow + PaperExecutor overlay:** named in Files
  (`fsync: none` is shadow-only; compose's `ShadowExecutor` is not the
  fill venue). `RUNG_TABLE["paper"].executor` is `'paper'` but
  `_RUNG_RULES["paper"].fsync_none` is False. Not a Files mismatch.
- **RecordedIdSource vs ReleaseIdSource:** compose tape injection
  returns `RecordedIdSource(())`; empty tape would exhaust on
  `next_tick_id`. Overlay `ReleaseIdSource` is named (not a recorded
  series). Required given empty `id_allocations`.
- **Trees:** README names `replay.py`; AGENTS/CLAUDE configs-line names
  fill-policy and development-replay. No new package files this cycle
  beyond the method memo (this architecture memo is the only file this
  reviewer may write).
- **Journal A18877:** follows A18876 (`next_id = max+1`); unique; README
  latest-10 slid. Product/journal/method authors are Cursor Grok 4.6.
- **No `__all__` leak of `_` names.** Child does not subclass
  `ServeLoop`. SchwabCostModel still extracted in the child. No
  `dskit.production` hook.
- **§11 items 4, 7, 8, 9, 10:** not silently ruled.

## What this lens did not review

Fill-timing arithmetic, `forced_exit_price_field` vs recorded exit
price, Tick `failed` / `InvalidOperation` vs `_fault`, override/queue
fill-drop behavior, halt/expiry edge cases, fee cents — reviewer 1,
method lens (`90ea3ce`, FAIL 0C 1M 3m). This review used those tests
only as evidence of what engine they exercise (`compose.bundles_for` +
`ServeLoop` + `LegPipeline`, not a private `enumerate` walk and not
sixteen stubs). Passing method tests of a dummy collaborator graph would
still not have been `compose.bundles_for`; that graph is gone.

## Handoff

PASS for this lens. Cycle-4's composition Major is closed: compose is
the root, `_TapeCadence` is a `Cadence`, the sixteen safety stubs are
gone, and the safety spine passed to `ServeLoop` is compose's. Do not
read this as method-clean (reviewer 1 FAIL stands) or as a durable
series (scratch `rmtree`; Phase 5 items 3–5 unbuilt; ADR-0117 still
**proposed**). Keep Status `proposed` until the owner accepts
overlap/suffix/queue/override. Optional follow-ups are the Nits
(honest cadence `uses` via a params-shaped child `Cadence`, distinct
release digests, drop dead `_TapeEntry` ticks, pin the overlays in the
AST test). Do not flip ADR Status; do not edit `path.csv`; do not add a
production cadence kind.

**Verbatim verdict:** `PASS — 0 Critical, 0 Major, 8 Nit.`
