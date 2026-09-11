## TL;DR

**PASS — 0 Critical, 0 Major, 11 Nit.**

Cycle-5's composition PASS still holds after `5a0c89e`: `bundles_for`
count 1, `tape=tape` no `clock=`, `_TapeCadence` is a `Cadence`, the
thirteen safety stubs stay gone, `ServeLoop` ticks compose's
safety/lease/health/ledger/`PaperAccounting`, fills go through
`LegPipeline`. Cycle-6's `recording.ledger.scan(kind="tick")` is a
post-run fail-loud read of the still-open compose `JsonlLedger` (no
fold, no `Recovery`, no `SeriesState.apply`). Leftover `_queued` /
`_pending_by_id` is the named overlay book, not a second fold.
Skip-halt quotes are child feed policy; `Tick.quotes` still builds
`QuoteSet`. Method FAIL (non-finite `open` / `refused` tick) is out of
scope (reviewer 1, cycle-6).

## Skeptic review — Gate 5 development replay (architecture lens, cycle 6)

**Reviewer task:** Gate 5 stateful replay / fill-policy, reviewer 2 of 2,
re-review after cycle-6 product `5a0c89e`
**Model/effort:** Cursor Grok 4.6
**Lens:** architecture / governance / tiering / OOP / file-ownership /
ServeLoop composition ONLY. Method/fill-timing/fee cents/NaN-inf
`open` / halt types are out of scope — reviewer 1 already filed
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle6.md`
(525ea30; FAIL 0C 1M 3m).
**Dispatch mode:** sequential — reviewer 2 of 2, independent of the method
lens. Prior architecture PASS:
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle5.md`
(57a397e). Re-prove that PASS after the cycle-6 method fix; hunt NEW
architecture defects the fix introduced (child `ledger.scan`, leftover
queue as a second composition concern, quotes skip-halt as child-owned
feed policy).

## Scope and commits reviewed

Product `5a0c89e355ba8f6837eaa659ddd5d45a4978d965`, journal
`7cb83ed32e08926a18afbf122443f60112f711c7` (A18879), method memo
`525ea30234672ac35123a16acca46f52346b236a` (HEAD). Base for the Gate 5
span: `d39e94c`. Kickoff read from
`origin/claude/dskit-merge-reviewed-branches-de0z8z:children/intraday_equities/docs/plans/2026-09-10-gate5-replay-kickoff.md`.
ADR-0117 Status / Files / Consequences at HEAD (untouched by `5a0c89e`);
`replay.py`; `configs/run-development-replay.json`; both child trees;
journal A18879. Cycle-5 architecture memo is the prior verdict to
re-prove, not a finding to rubber-stamp.

Owner-ruled 2026-09-10 waiver (kickoff + ADR Consequences): synthetic
tests of the named overlap/suffix/queue/override book may run with
`deployment_eligible=false` while ADR-0117 stays **proposed**. Status flip
by an agent would itself be a defect. Ungraded composition theater is not
covered by that waiver. Cycle-6 added no production hook and no
`ServeLoop` subclass.

What I tried to break: cycle-5 claims `bundles_for(tape=tape)` plus
overlays still hold; cycle-6 `scan` owns recovery/fold; leftover raise
replaces `ServeLoop` tick status; skip-halt bypasses compose `QuoteSet`.
Passing `test_replay.py` is not that proof. AST + wraps on
`ServeLoop.run` / `Tick.run` / `Tick.quotes` / `LegPipeline.run` /
`PaperExecutor.submit` / `compose.bundles_for` / `ReplayClock.__init__` /
`JsonlLedger.scan` / `JsonlLedger.close` / `SeriesState.apply` /
`Recovery.run` / `LedgerHistory.ticks` plus a `period_ms=5000`
monkeypatch, a halt-skip QuoteSet capture, a `ProductionError` in
`quotes()` after evaluate queued, and a `RuntimeError` failed tick are.

## Commands run and results

```text
$ git diff --stat d39e94c..HEAD
 children/intraday_equities/AGENTS.md               |    4 +-
 children/intraday_equities/CLAUDE.md               |    4 +-
 children/intraday_equities/README.md               |    1 +
 .../intraday_equities/configs/fill-policy.json     |    4 +-
 .../configs/run-development-replay.json            |    4 +-
 .../intraday_equities/docs/decisioning/README.md   |    8 +-
 .../intraday_equities/docs/decisioning/actions.csv |    4 +
 ...-skeptic-architecture-cycle4.md                 |  319 ++++++
 ...-skeptic-architecture-cycle5.md                 |  395 +++++++
 ...-skeptic-method-cycle4.md                       |  241 +++++
 ...-skeptic-method-cycle5.md                       |  227 ++++
 ...-skeptic-method-cycle6.md                       |  247 +++++
 .../intraday_equities/intraday_equities/replay.py  | 1099 ++++++++++++++++----
 children/intraday_equities/tests/test_replay.py    |  251 ++++-
 docs/architecture/decision-log.md                  |   41 +-
 15 files changed, 2594 insertions(+), 255 deletions(-)

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

Identity hash **unchanged** from cycle-5 `5adac27e…` (fill-policy digest
pin still `696b1bcf…`). Product `5a0c89e` touches only `replay.py` and
`test_replay.py` (ADR-0117 Files/Status untouched). Journal `7cb83ed` is
A18879. Method memo `525ea30` is docs-only.

AST of `replay.py` at HEAD (`/tmp/gate5_arch_c6_proof.py`):

```text
Call ServeLoop        count=1  line 640
Call bundles_for      count=1  line 612
Call PaperExecutor    count=1  line 627
Call ReleaseIdSource  count=1  line 638
Call BarTape          count=1  line 590
Call HorizonBook      count=1  line 497
Call EquityReplay     count=1  line 1191
Call ReplayAdapter    count=1  line 1365
Call _TapeCadence     count=1  line 634
Call Data             count=1  line 635
Call LegPipeline      count=0  (Tick._legs constructs it)
Call ReplayFeed       count=0  (compose._TapeInjection)
Call ReplayClock      count=0  (compose._TapeInjection)
Call ManualTime       count=0  (compose._TapeInjection)
Call JsonlLedger      count=0  (compose ledger_class)
Call PaperAccounting  count=0  (compose accounting.uses=paper)
Call RecordedIdSource count=0  (compose tape injection; overlaid away)
Call QuoteSet         count=0  (Tick.quotes constructs it)
Call Recovery         count=0  (ServeLoop._start, not the child)
Call LedgerHistory    count=0  (child does not use the named reader)
Call GENESIS_HASH     count=0
Call FixedInterval    count=0  (compose builds it, child never names it)
Call ledger.scan      count=1  line 646  (NEW cycle-6)
Call .apply           count=0
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
ServeLoop subclass    False
bundles_for kwargs    serve_root, secrets, invocation, process_id,
                      lock, journal_hook, tape=tape   (no clock=)
_run_loop order       run@644, scan@646, close@654, release@655
dummy/private classes: 5
  _HashView _TapeCadence _TapeEntry _TapeHead _PaperVenue
ABSENT vs cycle-4's 16:
  _Ready _Go _Heart _Metrics _Alerts _Breaker _Arming _Guards
  _Accounting _Lease _Reconcile _Verifier _AuthorityTable
quotes() calls _halted  True
leftover = list(_queued)+list(_pending_by_id)  present
scan status=="failed" only; "refused" absent from the scan
```

Runtime object graph (h1 buy, three bars at 1000/2000/3000 ms; wraps on
`ServeLoop.run`, `Tick.run`, `Tick.quotes`, `LegPipeline.run`,
`PaperExecutor.submit`, `ShadowExecutor.submit`, `compose.bundles_for`,
`ReplayClock.__init__`, `FixedInterval.next_tick`, `_TapeCadence.next_tick`,
`_TapeEntry.run`, `_TapeHead.run`, `Decider.prepare`/`read_entry`/`evaluate`,
`Readiness.evaluate`, `JsonlLedger.scan`/`close`, `SeriesState.apply`,
`Recovery.run`, `LedgerHistory.ticks`):

```text
ServeLoop.run              1   exit code 0
Tick.run                   3   at_ms [1000, 2000, 3000] status decided
LegPipeline.run            2   result='filled'
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
ManualTime shared          True (clock.time is feed._time; both ManualTime)
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
same_ledger                True  (compose JsonlLedger; same id)
same_readiness             True
same_feed                  True  (ReplayFeed)
same_clock                 True  (ReplayClock)
same_decision              True
same_observability         True
verifier._executor         ShadowExecutor  (compose's)
source_tokens shadow/paper (None, None) ==
snapshot.positions         ()
snapshot.pending           ()
doc cadence.uses           fixed-interval  period_ms=1000
CADENCE_KINDS              aligned-bar, at-times, fixed-interval, on-data
                           (no tape-times)
fills                      entry@2000 price 11.0; exit@3000 price 12.0
period_ms patched to 5000  ticks still [1000, 2000, 3000]; ticks_moved False
                           FixedInterval.next_tick still 0
EquityReplay subclass Loop False  (MRO EquityReplay, object)
DevelopmentReplay subclass False  (Node)

JsonlLedger.scan           2   kind=None (Recovery at start, apply_before=0);
                               kind=tick (child after loop.run, apply_before=23)
scan same_as_compose       True both; type JsonlLedger; closed False
scan_apply_during child    0   (no SeriesState.apply while scanning ticks)
scan_tick_statuses         decided, decided, decided
JsonlLedger.close          1   after both scans
Recovery.run               1   ServeLoop._start, not the child
LedgerHistory.ticks        0   child did not use the named reader
Tick.quotes                3   each returns QuoteSet (child Call QuoteSet=0)
```

`DevelopmentReplay.run` still ends in `ReplayAdapter.replay` →
`EquityReplay.run` → `_run_loop` → `bundles_for` → overlays →
`ServeLoop(...)`; `loop.run()`; then cycle-6 `scan` / leftover / close.
Fills go through `LegPipeline`. Cycle-4 Major ingredients stay closed.

## Cycle-5 re-test

| Cycle-5 (`673377e` / 57a397e) | Cycle-6 (`5a0c89e`) |
|---|---|
| `bundles_for` AST+runtime count **1**, `tape=tape`, no `clock=` | **Still holds.** AST 1 line 612; runtime 1; kwargs `tape=BarTape`; `clock_kw False` |
| `_TapeCadence` bases `('Cadence',)`; isinstance True | **Still holds.** |
| Thirteen safety stubs gone | **Still holds.** Remaining `_HashView` / `_TapeCadence` / `_TapeEntry` / `_TapeHead` / `_PaperVenue` |
| Dummy `digest="a"*64` / `account=None` / `enumerate(seq)` | **Still absent** (`enumerate(seq)` only in `_index_of`) |
| Safety/lease/health/ledger/`PaperAccounting` are compose's | **Still holds.** `same_*=True`; lease `ProcessLease`; accounting `PaperAccounting` |
| `LegPipeline.run` 2, `PaperExecutor.submit` 2, `ShadowExecutor.submit` 0 | **Still holds.** |
| No `ServeLoop` subclass; no production hook; `path.csv` 0 | **Still holds.** MRO `(EquityReplay, object)`; `dskit/production` diff 0 bytes; both `path.csv` 0 bytes |
| Lying `fixed-interval` 1000 ms while tape fires | **Still true; still Nit.** `period_ms=5000` ticks `[1000,2000,3000]` |
| Compose cadence/decider/executor/id_source overlaid | **Still the ADR-named equity seam**, not a remaining Major |

## Cycle-6 delta — hunted, graded

### `recording.ledger.scan(kind="tick")` after `loop.run()`

**Does scan happen while the ledger is still the compose `JsonlLedger`?**
Yes. `recording = replace(recording, id_source=ReleaseIdSource(...))`
keeps the same ledger object (`same_ledger True`). Wrap of
`JsonlLedger.scan`: child's `kind="tick"` call has `id` equal to
`compose_ledger_id`, `type='JsonlLedger'`, `closed False`. AST order is
`loop.run` → `scan` → `ledger.close` → `lock.release`. Close count 1
after both scans.

**Is scanning the chain from a child a §5.8.1 violation?**
§5.8.1: `SeriesState` is the sole fold; nothing else folds.
`tests/production/test_state.py` `SCAN_READERS` =
`{state.py, ledger.py, reconcile.py, __main__.py, outcomes.py, report.py}`.
`loop.py` `_process_id`: "the loop is not one of the modules that may
scan the chain (§5.8.1)". `LedgerHistory` is "the one reader"
(`reconcile.py`). Production `report.Replay` is a named SCAN_READER and
also scans after `ServeLoop.run()`.

Child AST: `Recovery` 0, `LedgerHistory` 0, `.apply` 0, `ledger.scan` 1.
Runtime: `Recovery.run` 1 is `ServeLoop._start` (its `kind=None` scan is
the other of the two `JsonlLedger.scan` calls). Child tick-scan
`scan_apply_during=0`. `LedgerHistory.ticks` 0. No assignment to
`FOLDED_ATTRS`. The scan reads already-recorded `tick` envelopes for
`body.status == "failed"` and raises; it does not replay `SeriesState.apply`,
does not close open ticks, does not own recovery.

Prompt rubric: **Major** if the child now owns recovery/fold semantics;
**Nit** if it is a post-run fail-loud check that does not fold state.
Proof puts this in **Nit** (finding 9). It is "a second module that
learned to scan" relative to `LedgerHistory.ticks`, but it is not a
second fold and not composition theater of the cycle-4 kind.

### Leftover `_queued` / `_pending_by_id` raise

**Does leftover-queue raise replace `ServeLoop`'s own tick status?**
On the chain: no. `Tick.run` still records `status`/`error`; leftover
does not append or rewrite. On the child's public `ConfigError`: it can
hide a recorded `refused`. Injected `ProductionError` in `quotes()`
after evaluate queued: tick statuses `decided, refused, refused`; scan
sees those statuses and does not raise (`status=="failed"` only);
leftover raise fires `ConfigError: 4 fill(s) queued but never submitted`
— the recorded refused reason never appears. Failed ticks are checked
first: injected `RuntimeError` in `quotes()` yields
`ServeLoop tick status failed: RuntimeError: …` and leftover is not
reached. Happy path: `snapshot.pending ()` and leftover empty.

`_queued` / `_pending_by_id` are the named equity overlay book (cycle-5
already had them; `proposals()` drains `_queued`; `_on_ack` pops
pending). Cycle-6 only fail-louds leftover after `loop.run()`. That is
a second *success criterion* (overlay drained) beside `ServeLoop` exit
code 0, not a second `SeriesState`. Concatenating `list(_queued)` with
`list(_pending_by_id)` (keys) double-counts. Graded **Nit** (finding 10),
not Major: no fold, no recovery, overlay already named in Files.

### Quotes skip `_halted`

**Did skip-halt quotes bypass compose `QuoteSet` rules?**
No. Child `Call QuoteSet` 0. `Tick.quotes` still assembles `QuoteSet`
from `proposer.quotes()` (loop.py: empty set is legal; `min_asof_ms`
defaults to `clock.now_ms()`). Halt AAA + live BBB at 2000: QuoteSets
`[['AAA','BBB'], ['BBB'], ['AAA','BBB']]` — halted name omitted from
the set, remaining quotes still `Quote` records, ticks `decided`.
Compose has no rule that `QuoteSet` must include every `EntryBatch`
name. Skip is child-owned feed policy through the named proposer
overlay (`Data(feed=data.feed, decider=self)`); ADR-0117 already names
halt skip. The method FAIL (halted non-finite `open` still in
`EntryBatch` via `read_entry`) is reviewer 1's lens, not a QuoteSet
bypass.

## Findings

### Cycle-5 Major — still closed (not re-litigated as open)

Gate 5a: children must not subclass `ServeLoop`. They do not
(`EquityReplay` MRO is `(EquityReplay, object)`; no `class …(ServeLoop)`
in the child). Kickoff: compose `dskit.production`; reuse `ServeLoop` +
`ReplayFeed` + replay clock + paper executor + ledger. Runtime
`bundles_for` count is **1** with `tape=BarTape`. Compose's
`_TapeInjection` supplies one shared `ManualTime` to `ReplayClock` and
`ReplayFeed` (`clock.time is feed._time`). The thirteen always-GO stubs
are gone; `ServeLoop` holds compose's `Safety` / `Health` /
`ProcessLease` / ledger / `Readiness` / `PaperAccounting` / `Decision` /
`Observability` (identity-equal to the tuple `bundles_for` returned).
`_TapeCadence` is a `Cadence`. `PaperExecutor` is constructed on
**compose's clock** and wrapped as `_PaperVenue`; `LegPipeline.run` 2,
`PaperExecutor.submit` 2, `ShadowExecutor.submit` 0. ADR-0117 Files
(unchanged by cycle-6) names `bundles_for(..., tape=BarTape)`, the four
overlays, shadow-vs-paper, `ReleaseIdSource`, `HorizonBook`, no
`ServeLoop` subclass, no production hook, `path.csv` untouched. Status
line remains `proposed (2026-09-10)`.

Overlays throw away compose's `FixedInterval`, `Decider`,
`ShadowExecutor`, and `RecordedIdSource`. That is still the **ADR-named
equity seam**, not a remaining Major.

### Cycle-6 — no new Critical or Major

Child `ledger.scan` / leftover raise / skip-halt quotes were hunted as
composition defects. Proof: scan is a post-run read of compose's still-open
`JsonlLedger` with `SeriesState.apply` count 0 during the tick scan;
leftover is fail-loud on the named overlay book; skip-halt does not
construct `QuoteSet` and does not skip `Tick.quotes`. None of those own
recovery/fold semantics. See Nits 9–11.

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
   `aligned-bar, at-times, fixed-interval, on-data` (no tape-times).
   `CADENCE_KINDS.resolve("intraday_equities.replay:_TapeCadence")`
   still succeeds (child path). Named overlay; dummy `uses` is a
   choice.

2. **One fill-policy digest reused as every release identity slot.**
   `policy.digest()` = `696b1bcf…` (run-document pin matches).
   `Readiness.evaluate` count 0; no `readiness.json` exists in the
   repo; D24's checklist pin is unexercised theater on this unarmed
   path.

3. **`_TapeEntry` / `_TapeHead` never tick.** `Decider.prepare` 1 during
   `bundles_for`; both `run` counts 0; compose `Decider.read_entry` /
   `evaluate` 0 after `Data(feed=data.feed, decider=self)`. Prepare-only
   graph so compose will classify a serving run, then the equity overlay
   throws that `Decider` away. Files does not name these nodes.

4. **Verifier/breaker still hold compose's `ShadowExecutor`.**
   `safety.submission_verifier._executor` type `ShadowExecutor`; loop
   `execution.executor` is `_PaperVenue`. Submit path is Paper
   (`PaperExecutor.submit` 2, `ShadowExecutor.submit` 0).
   `source_tokens` on both is `(None, None)`, so the split does not
   refuse. Files names replacing the fill venue, not rebinding the
   verifier.

5. **`EquityReplay` is a public-looking class and is not in `__all__`**
   (`BarTape`, `DevelopmentReplay`, `FillPolicy`, `HorizonBook`,
   `ReplayAdapter` only). Package `__init__.py` also omits it. No
   underscore name leaks into `__all__`.

6. **`_HashView` is still duplicated** in `replay.py` and
   `tests/test_replay.py`.

7. **Extra `Co-authored-by: gdrusse` trailer** remains on `5a0c89e`,
   `7cb83ed`, and `525ea30`; author line itself is Cursor Grok 4.6.

8. **`test_replay_py_composes_serveloop_replayfeed_tape_and_shared_clock`**
   still AST-pins `bundles_for`, `Cadence` in bases, `tape=tape`, and
   absence of `class _Ready` / `class _Accounting` / `enumerate(seq)` /
   `"a"*64`. It does not pin dummy-hex collapse, `period_ms`
   independence, `FixedInterval.next_tick==0`, verifier-vs-submit
   executor identity, child `ledger.scan` vs `LedgerHistory`, or
   leftover vs `SeriesState.pending`.

9. **Child `recording.ledger.scan(kind="tick")` is a second chain reader.**
   §5.8 / `SCAN_READERS` name `LedgerHistory` (and `report.py`'s replay
   verb), not a child. `loop.py` forbids the loop itself from scanning.
   The child walks envelopes for `body.status == "failed"` instead of
   `LedgerHistory.ticks(0)`. Proven post-run, same compose `JsonlLedger`,
   `closed False`, `SeriesState.apply` 0 during the tick scan, `Recovery`
   not called by the child. Fail-loud, not fold. Nit not Major.

10. **Leftover overlay can hide recorded `refused` in the public raise.**
    `leftover = list(self._queued) + list(self._pending_by_id)` is child
    fill-queue state, not `SeriesState.pending` (happy path both empty).
    After a `ProductionError` in `quotes()` once evaluate queued: ledger
    ticks `decided, refused, refused`; child raises
    `4 fill(s) queued but never submitted` (queued dicts concatenated
    with pending keys, so the count doubles). Failed ticks still win
    (`RuntimeError` → `ServeLoop tick status failed: …`). Second
    composition concern (overlay drained vs recorded tick status); does
    not fold; does not rewrite the chain. Nit not Major.

11. **ADR-0117 Files is silent on the post-run scan and leftover raise.**
    Files still names `bundles_for` + the four overlays. Cycle-6 did not
    edit the ADR (correct: Status stays proposed). Residual honesty:
    the new fail-loud is runtime-only.

## Items checked, not defective under this lens

- **Cycle-5 composition PASS:** re-proved above. `DevelopmentReplay.run`
  → `ReplayAdapter.replay` → `EquityReplay.run` → `bundles_for` →
  `ServeLoop.run`. Fills go through `LegPipeline`.
- **`dskit/production`:** zero-byte diff on `d39e94c..HEAD`. Gate 5a (no
  generic hook). Correct.
- **`path.csv`:** repo-root and child, 0 bytes diff. Agents did not edit
  Path.
- **ADR-0117 Status:** remains `proposed (2026-09-10)` at product and
  HEAD. Cycle-6 did **not** flip Status and did **not** edit Files.
- **Files vs runtime (overlays):** Files names `bundles_for(..., tape=BarTape)`,
  cadence/`Cadence` overlay, equity decider, `ReleaseIdSource`,
  `PaperVenue` around `PaperExecutor` on compose's clock, `rung=shadow`
  because `fsync: none` is shadow-only, `LegPipeline`, no `ServeLoop`
  subclass, no production hook, `HorizonBook` as `(symbol, lead)`.
  Runtime matches those sentences. `snapshot.positions` is `()` by that
  named split.
- **Skip-halt quotes:** child-owned feed policy on the named proposer
  overlay. `Tick.quotes` still builds `QuoteSet`. Empty sets are
  compose-legal. Not a QuoteSet bypass. Halted-nan-in-`EntryBatch` is
  method (reviewer 1).
- **Failed-tick raise:** wraps `ServeLoop`'s already-recorded
  `status=failed`; does not replace `Tick.run`. Injected `RuntimeError`
  proves the scan reads compose's chain and raises the recorded class/text.
- **Named overlap/suffix/queue/override while proposed:** still in
  `_VOCAB` / JSON / ADR. `deployment_eligible` still refused unless JSON
  false. Waiver applies; not a Critical.
- **Phase 5 items 3–5 / scratch `mkdtemp`+`rmtree` ledger:** AST
  `mkdtemp` 1 / `rmtree` 1. ADR Consequences already say crash/restart
  ledger identity, post-fill solvency, and live-graph unfunded
  candidates remain unbuilt. Not re-raised as Critical.
- **`GENESIS_HASH` / `"0"*64`:** absent from `replay.py` (compose's
  `ledger.head()` supplies the checkpoint).
- **paper rung vs shadow + PaperExecutor overlay:** named in Files.
- **RecordedIdSource vs ReleaseIdSource:** overlay named (not a recorded
  series). Required given empty `id_allocations`.
- **Trees:** README names `replay.py`; AGENTS/CLAUDE configs-line names
  fill-policy and development-replay. No new package files this cycle
  beyond the method memo (this architecture memo is the only file this
  reviewer may write).
- **Journal A18879:** follows A18878 (`next_id = max+1`); unique; README
  latest-10 slid. Product/journal/method authors are Cursor Grok 4.6.
- **No `__all__` leak of `_` names.** Child does not subclass
  `ServeLoop`. SchwabCostModel still extracted in the child. No
  `dskit.production` hook.
- **§11 items 4, 7, 8, 9, 10:** not silently ruled.

## What this lens did not review

Fill-timing arithmetic, `forced_exit_price_field` vs recorded exit
price, non-finite `open` / tick status `refused` peer-fill drop, fee
cents, override/queue fill-drop behavior, halt/expiry edge cases —
reviewer 1, method lens (`525ea30`, FAIL 0C 1M 3m). This review used
those tests only as evidence of what engine they exercise
(`compose.bundles_for` + `ServeLoop` + `LegPipeline`, not a private
`enumerate` walk and not sixteen stubs). The method FAIL's `refused`
tick (EntryBatch non-finite `open`) is exactly the status the cycle-6
scan does not watch; that is a method hole, not an architecture
re-opening of cycle-4's dummy spine.

## Handoff

PASS for this lens. Cycle-5's composition PASS still holds after the
cycle-6 method fix: compose is the root, `_TapeCadence` is a `Cadence`,
the sixteen safety stubs are gone, and the safety spine passed to
`ServeLoop` is compose's. Cycle-6's `ledger.scan` is a post-run
fail-loud read of compose's `JsonlLedger`, not child-owned recovery.
Leftover is fail-loud on the named overlay book. Skip-halt quotes do
not bypass `Tick.quotes`/`QuoteSet`. Do not read this as method-clean
(reviewer 1 FAIL stands) or as a durable series (scratch `rmtree`;
Phase 5 items 3–5 unbuilt; ADR-0117 still **proposed**). Keep Status
`proposed` until the owner accepts overlap/suffix/queue/override.
Optional follow-ups are the Nits (route tick-status through
`LedgerHistory.ticks`, raise on `refused` as well as `failed` if this
replay has no honest refused tick — that is also the method Major —
honest cadence `uses`, distinct release digests). Do not flip ADR
Status; do not edit `path.csv`; do not add a production cadence kind.

**Verbatim verdict:** `PASS — 0 Critical, 0 Major, 11 Nit.`
