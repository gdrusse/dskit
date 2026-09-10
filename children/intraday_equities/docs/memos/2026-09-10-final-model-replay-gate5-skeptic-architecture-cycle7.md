## TL;DR

**PASS — 0 Critical, 0 Major, 12 Nit.**

Cycle-6's composition PASS still holds after `5914052`: `bundles_for`
count 1, `tape=tape` no `clock=`, `_TapeCadence` is a `Cadence`, the
thirteen safety stubs stay gone, `ServeLoop` ticks compose's
safety/lease/health/ledger/`PaperAccounting`, fills go through
`LegPipeline`. `_refuse_non_finite_price` is a child ingest subset of
production `EntryBatch` JSON/finite rules and **fail-loud-gates before
the loop** for Python/Decimal nan-inf on the three named price fields
(`ServeLoop.run=0`). Values production would refuse that the child
accepts besides named halt-skip `None`/`''` (`numpy.float32`, unused
`high=nan`) still abort via EntryBatch `refused` + the post-run scan —
not a silent green. Scan now watches `refused` as well as `failed`; it
is still a post-run read of the still-open compose `JsonlLedger`
(`SeriesState.apply` 0 during the tick scan; no fold). Method PASS
(nan/fees) is out of scope (reviewer 1, cycle-7).

## Skeptic review — Gate 5 development replay (architecture lens, cycle 7)

**Reviewer task:** Gate 5 stateful replay / fill-policy, reviewer 2 of 2,
re-review after cycle-7 product `5914052`
**Model/effort:** Cursor Grok 4.6
**Lens:** architecture / governance / tiering / OOP / file-ownership /
ServeLoop composition ONLY. Method/fill-timing/fee cents/NaN-inf
`open` as fill correctness are out of scope — reviewer 1 already PASSed
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle7.md`
(831a292; PASS 0C 0M 4m).
**Dispatch mode:** sequential — reviewer 2 of 2, independent of the method
lens. Prior architecture PASS:
`children/intraday_equities/docs/memos/2026-09-10-final-model-replay-gate5-skeptic-architecture-cycle6.md`
(f19c81a). Re-prove that PASS after the cycle-7 ingest refuse; hunt NEW
architecture defects the fix introduced (`_refuse_non_finite_price`
duplicating production EntryBatch money/JSON rules; refused-tick scan
widening the child as a second ledger reader). Cycle-6 nits re-graded
only if the delta made one a Major (second composition root). None did.

## Scope and commits reviewed

Product `59140520aa661d4f35c2cc59749a0f2dbe349ace`, journal
`140e387b2a49df3590d178b175960b2c0fe6871f` (A18879), method memo
`831a2929d5b63b25311c46bce98ff21bd15c5e6c` (HEAD). Base for the Gate 5
span: `d39e94c`. Kickoff read from
`origin/claude/dskit-merge-reviewed-branches-de0z8z:children/intraday_equities/docs/plans/2026-09-10-gate5-replay-kickoff.md`.
ADR-0117 Status / Files / Consequences at HEAD (untouched by `5914052`);
`replay.py`; `configs/run-development-replay.json`; both child trees;
journal A18879. Cycle-6 architecture memo is the prior verdict to
re-prove, not a finding to rubber-stamp.

Owner-ruled 2026-09-10 waiver (kickoff + ADR Consequences): synthetic
tests of the named overlap/suffix/queue/override book may run with
`deployment_eligible=false` while ADR-0117 stays **proposed**. Status flip
by an agent would itself be a defect. Ungraded composition theater is not
covered by that waiver. Cycle-7 added no production hook and no
`ServeLoop` subclass.

What I tried to break: cycle-6 claims `bundles_for(tape=tape)` plus
overlays still hold; `_refuse_non_finite_price` silently disagrees with
`EntryBatch`/`_json_value`/`Quote` money; refused-tick scan now owns
recovery/fold or is a second ServeLoop root. Passing `test_replay.py` is
not that proof. AST + wraps on `ServeLoop.run` / `Tick.run` /
`Tick.quotes` / `LegPipeline.run` / `PaperExecutor.submit` /
`compose.bundles_for` / `ReplayClock.__init__` / `JsonlLedger.scan` /
`JsonlLedger.close` / `SeriesState.apply` / `Recovery.run` /
`LedgerHistory.ticks` plus a `period_ms=5000` monkeypatch, halt-skip
QuoteSet capture, `ProductionError` in `quotes()` after evaluate queued,
`RuntimeError` failed tick, ingest-vs-EntryBatch value matrix (including
`numpy.float32`), unused-field nan, and python/Decimal nan with
`ServeLoop.run` count.

## Commands run and results

```text
$ git diff --stat d39e94c..HEAD
 children/intraday_equities/AGENTS.md               |    4 +-
 children/intraday_equities/CLAUDE.md               |    4 +-
 children/intraday_equities/README.md               |    1 +
 .../intraday_equities/configs/fill-policy.json     |    4 +-
 .../configs/run-development-replay.json            |    4 +-
 .../intraday_equities/docs/decisioning/README.md   |   12 +-
 .../intraday_equities/docs/decisioning/actions.csv |    6 +
 ...-skeptic-architecture-cycle4.md                 |  319 ++++++
 ...-skeptic-architecture-cycle5.md                 |  395 +++++++
 ...-skeptic-architecture-cycle6.md                 |  523 +++++++++
 ...-skeptic-method-cycle4.md                       |  241 +++++
 ...-skeptic-method-cycle5.md                       |  227 ++++
 ...-skeptic-method-cycle6.md                       |  247 +++++
 ...-skeptic-method-cycle7.md                       |  223 ++++
 .../intraday_equities/intraday_equities/replay.py  | 1123 ++++++++++++++++----
 children/intraday_equities/tests/test_replay.py    |  274 ++++-
 docs/architecture/decision-log.md                  |   41 +-
 17 files changed, 3391 insertions(+), 257 deletions(-)

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

Identity hash **unchanged** from cycle-5/6 `5adac27e…` (fill-policy digest
pin still `696b1bcf…`). Product `5914052` touches only `replay.py` and
`test_replay.py` (ADR-0117 Files/Status untouched; diff vs HEAD on the
decision-log is 0 bytes). Journal `140e387` is A18879. Method memo
`831a292` is docs-only.

AST of `replay.py` at HEAD (`/tmp/gate5_arch_c7_proof.py`):

```text
Call ServeLoop        count=1  line 647
Call bundles_for      count=1  line 619
Call PaperExecutor    count=1  line 634
Call ReleaseIdSource  count=1  line 645
Call BarTape          count=1  line 597
Call HorizonBook      count=1  line 497
Call EquityReplay     count=1  line 1215
Call ReplayAdapter    count=1  line 1389
Call _TapeCadence     count=1  line 641
Call Data             count=1  line 642
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
Call ledger.scan      count=1  line 653  (cycle-6; predicate widened cycle-7)
Call .apply           count=0
Load compose          from dskit.production.compose import bundles_for
Load Cadence          from dskit.production.cadence import Cadence
Load number_ok        from dskit.pipeline.records import number_ok
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
                      lock, journal_hook, tape   (no clock=)
_run_loop order       run@651, scan@653, close@661, release@662
dummy/private classes: 5
  _HashView _TapeCadence _TapeEntry _TapeHead _PaperVenue
ABSENT vs cycle-4's 16:
  _Ready _Go _Heart _Metrics _Alerts _Breaker _Arming _Guards
  _Accounting _Lease _Reconcile _Verifier _AuthorityTable
quotes() calls _halted  True
leftover = list(_queued)+list(_pending_by_id)  present
scan status=="failed" or "refused"  (cycle-7 widened from failed-only)
_refuse_non_finite_price def+call  present line 522 (NEW cycle-7)
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
LegPipeline.run            2   result filled
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
same_ledger                True  (compose JsonlLedger; same id)
same_readiness             True
same_feed                  True  (ReplayFeed)
same_clock                 True  (ReplayClock)
same_decision              True
same_observability         True
verifier._executor         ShadowExecutor  (compose's)
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
`ServeLoop(...)`; `loop.run()`; then `scan` / leftover / close.
Fills go through `LegPipeline`. Cycle-4 Major ingredients stay closed.

## Cycle-6 re-test

| Cycle-6 (`5a0c89e` / f19c81a) | Cycle-7 (`5914052`) |
|---|---|
| `bundles_for` AST+runtime count **1**, `tape=tape`, no `clock=` | **Still holds.** AST 1 line 619; runtime 1; kwargs `tape`; `clock_kw False` |
| `_TapeCadence` bases `('Cadence',)`; isinstance True | **Still holds.** |
| Thirteen safety stubs gone | **Still holds.** Remaining `_HashView` / `_TapeCadence` / `_TapeEntry` / `_TapeHead` / `_PaperVenue` |
| Dummy `digest="a"*64` / `account=None` / `enumerate(seq)` | **Still absent** (`enumerate(seq)` only in `_index_of`) |
| Safety/lease/health/ledger/`PaperAccounting` are compose's | **Still holds.** `same_*=True`; lease `ProcessLease`; accounting `PaperAccounting` |
| `LegPipeline.run` 2, `PaperExecutor.submit` 2, `ShadowExecutor.submit` 0 | **Still holds.** |
| No `ServeLoop` subclass; no production hook; `path.csv` 0 | **Still holds.** MRO `(EquityReplay, object)`; `dskit/production` diff 0 bytes; both `path.csv` 0 bytes |
| Lying `fixed-interval` 1000 ms while tape fires | **Still true; still Nit.** `period_ms=5000` ticks `[1000,2000,3000]` |
| Compose cadence/decider/executor/id_source overlaid | **Still the ADR-named equity seam**, not a remaining Major |
| `ledger.scan` post-run, not a fold | **Still holds**, predicate now also `refused` (see cycle-7 delta) |

## Cycle-7 delta — hunted, graded

### `_refuse_non_finite_price` vs production EntryBatch / Quote

Child ingest (existing `EquityReplay.run` bar loop, beside `_halt_flag` and
duplicate-asof) now walks `fill_price_field` /
`forced_exit_price_field` / `decision_price_field` and calls
`_refuse_non_finite_price`. That helper: `None`/`''` return (named
halt-skip); `Decimal.is_finite()`; else `int`/`float` not bool via
imported `number_ok` (the pipeline owner, `replay_mod.number_ok is
number_ok`); other types return (accept). Production `EntryBatch.outputs`
is `_json_value`: any non-finite float/Decimal refuses; non-JSON types
(`numpy.float32`) refuse. `Quote.bid/ask/mid` are Decimal money.

Executed matrix (same object into child helper vs `EntryBatch(outputs=
{"records": [bar]})`):

| value | child ingest | EntryBatch | silent? |
|---|---|---|---|
| `None`, `""` | accept | accept | named halt-skip |
| `float('nan')`/`inf`/`-inf`, `math.nan`, `Decimal('NaN'/±Infinity)`, `np.nan`/`np.inf`, `np.float64(nan)` | refuse | refuse | no — same direction |
| `11`, `11.0`, `Decimal('11')`, `np.float64(11)` | accept | accept | no |
| `numpy.float32(nan)` and `float32(11)` | **accept** | **refuse** (`float32 is not a JSON value`) | **no** — replay still raises |
| unused `high=float('nan')` | ingest does not walk `high` | refuse (`records[0].high: non-finite`) | **no** — replay still raises |

Vice versa (child refuse / production accept) besides `None`/`''`: **no
row**.

Runtime, not the matrix: halt/live Python nan-inf and `Decimal('NaN')` on
`open` raise `ConfigError` naming `open` with `ServeLoop.run=0`,
`bundles_for=0`, `scan=0`, `TICKS []` — fail-loud **before** compose.
`numpy.float32('nan')` on halt `open`: child ingest accepts, then
`ServeLoop.run=1`, ticks `decided, refused`, scan raises
`ServeLoop tick status failed: refused: EntryBatch.outputs.records[0].open:
float32 is not a JSON value`, `apply_during_tick_scan=0`. Unused
`high=nan` on an otherwise finite `open`: same shape (`ServeLoop.run=1`,
`LegPipeline=0`, ticks `decided, refused`, scan names `high`). Halt
`open=None`: peer BBB fills **21.0**, AAA skipped, ticks `decided`,
QuoteSets `[['AAA','BBB'], ['BBB'], ['BBB']]`.

Prompt rubric: **Major** only if the child **silently** disagrees
(production would refuse and the child accepts, or vice versa, besides
named halt-skip) — a green success. Proof is fail-loud on every
disagreeing value exercised. **Nit** (finding 12): a second finite-number
predicate, scoped to three named fields and JSON int/float/Decimal, sitting
in front of production's full JSON walk. It imports `number_ok` rather
than copying `math.isfinite`. Kickoff forbids a production hook; this is
the allowed child-tape gate, not a second `ServeLoop`.

### `scan` now treats `refused` like `failed`

AST still one `ledger.scan` (line 653), order still
`loop.run` → `scan` → `close` → `lock.release`. Runtime happy path:
child `kind="tick"` call has the same `id` as compose's `JsonlLedger`,
`closed False`, `apply_before=23`, `apply_during_tick_scan=0`.
`Recovery.run` 1 is `ServeLoop._start` (`kind=None` scan).
`LedgerHistory.ticks` 0. No `.apply` in the child AST. `SCAN_READERS`
still `{state.py, ledger.py, reconcile.py, __main__.py, outcomes.py,
report.py}` — the child is not one of them, and is still not a fold.

Widening the predicate does **not** make the child own recovery. It is
the same post-run success criterion cycle-6 already had for `failed`,
now also aborting recorded `refused` (Tick.run: `ProductionError` →
status `refused`, a value; only the loop continues). On this adapter
`BarTape` feed status is always `live`, so feed-dead `refused` does not
appear; unused-field / float32 poison is the `refused` this delta is
for. Injected `ProductionError` in `quotes()` after evaluate queued:
ticks `decided, refused, refused`; public raise is
`ServeLoop tick status failed: refused: injected quotes refuse`;
`leftover_in_msg False` — cycle-6 Nit 10 leftover-hiding-refused is
**closed**. Injected `RuntimeError`: `failed` still wins
(`RuntimeError: injected quotes fail`). `apply_during_tick_scan=0` on
both.

Prompt rubric: **Major** if the child now owns recovery/fold or is a
second composition root; **Nit** if post-run fail-loud that does not
fold. Proof stays **Nit** (finding 9, widened). Do not escalate: a
wider read of the same compose chain is not a second root.

## Findings

### Cycle-6 Major — still closed (not re-litigated as open)

Gate 5a: children must not subclass `ServeLoop`. They do not
(`EquityReplay` MRO is `(EquityReplay, object)`; no `class …(ServeLoop)`
in the child). Kickoff: compose `dskit.production`; reuse `ServeLoop` +
`ReplayFeed` + replay clock + paper executor + ledger. Runtime
`bundles_for` count is **1** with `tape=BarTape`. Compose's
`_TapeInjection` supplies one shared `ManualTime` to `ReplayClock` and
`ReplayFeed`. The thirteen always-GO stubs are gone; `ServeLoop` holds
compose's `Safety` / `Health` / `ProcessLease` / ledger / `Readiness` /
`PaperAccounting` / `Decision` / `Observability` (identity-equal to the
tuple `bundles_for` returned). `_TapeCadence` is a `Cadence`.
`PaperExecutor` is constructed on **compose's clock** and wrapped as
`_PaperVenue`; `LegPipeline.run` 2, `PaperExecutor.submit` 2,
`ShadowExecutor.submit` 0. ADR-0117 Files (unchanged by cycle-7) names
`bundles_for(..., tape=BarTape)`, the four overlays, shadow-vs-paper,
`ReleaseIdSource`, `HorizonBook`, no `ServeLoop` subclass, no production
hook, `path.csv` untouched. Status line remains `proposed (2026-09-10)`.

Overlays throw away compose's `FixedInterval`, `Decider`,
`ShadowExecutor`, and `RecordedIdSource`. That is still the **ADR-named
equity seam**, not a remaining Major.

### Cycle-7 — no new Critical or Major

Child ingest finite-gate / refused-tick scan were hunted as composition
defects. Proof: ingest is a fail-loud subset of production's JSON/finite
walk and never starts `ServeLoop` on Python/Decimal nan-inf; disagreeing
values (`float32`, unused `high=nan`) still fail-loud via EntryBatch
`refused` + the same post-run compose-ledger scan (`apply` 0 during that
scan). Scan is still not child-owned recovery. See Nits 9–12.

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
   choice. Cycle-7 did not make this a second root.

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
   (`PaperExecutor.submit` 2, `ShadowExecutor.submit` 0). Files names
   replacing the fill venue, not rebinding the verifier.

5. **`EquityReplay` is a public-looking class and is not in `__all__`**
   (`BarTape`, `DevelopmentReplay`, `FillPolicy`, `HorizonBook`,
   `ReplayAdapter` only). Package `__init__.py` also omits it. No
   underscore name leaks into `__all__`.

6. **`_HashView` is still duplicated** in `replay.py` and
   `tests/test_replay.py`.

7. **Extra `Co-authored-by: gdrusse` trailer** remains on `5914052`,
   `140e387`, and `831a292`; author line itself is Cursor Grok 4.6.

8. **`test_replay_py_composes_serveloop_replayfeed_tape_and_shared_clock`**
   still AST-pins `bundles_for`, `Cadence` in bases, `tape=tape`, and
   absence of `class _Ready` / `class _Accounting` / `enumerate(seq)` /
   `"a"*64`. It does not pin dummy-hex collapse, `period_ms`
   independence, `FixedInterval.next_tick==0`, verifier-vs-submit
   executor identity, child `ledger.scan` vs `LedgerHistory`, leftover
   vs `SeriesState.pending`, ingest-vs-EntryBatch, or `status=="refused"`.

9. **Child `recording.ledger.scan(kind="tick")` is still a second chain
   reader; cycle-7 only widened the predicate to `refused`.**
   §5.8 / `SCAN_READERS` name `LedgerHistory` (and `report.py`'s replay
   verb), not a child. `loop.py` still says the loop is not a module that
   may scan the chain. The child walks envelopes instead of
   `LedgerHistory.ticks(0)`. Proven post-run, same compose `JsonlLedger`,
   `closed False`, `SeriesState.apply` 0 during the tick scan, `Recovery`
   not called by the child. Fail-loud, not fold. Public message still
   says `ServeLoop tick status failed:` even when the recorded status is
   `refused`. Nit not Major — not a second root.

10. **Leftover overlay double-count remains; leftover-hiding-refused is
    closed.** `leftover = list(self._queued) + list(self._pending_by_id)`
    is still child fill-queue state, not `SeriesState.pending` (happy
    path both empty). After `ProductionError` in `quotes()` once evaluate
    queued: ledger ticks `decided, refused, refused`; child now raises
    the recorded refused text, not `N fill(s) queued but never submitted`
    (`leftover_in_msg False`). Failed ticks still win (`RuntimeError` →
    `ServeLoop tick status failed: RuntimeError: …`). Concatenation can
    still double-count if leftover is the raise that fires. Nit not
    Major.

11. **ADR-0117 Files is silent on the post-run scan, leftover raise, and
    ingest finite-gate.** Files still names `bundles_for` + the four
    overlays. Cycle-7 did not edit the ADR (correct: Status stays
    proposed). Residual honesty: the new fail-louds are runtime-only.

12. **`_refuse_non_finite_price` duplicates production's JSON/finite walk
    as a named-field subset.** It gates Python/Decimal nan-inf on
    `open`/`close` (the shipped fill/exit/decision fields) before
    `bundles_for`. It does not walk unused `high`/`low`/`volume` and
    returns on non int/float/Decimal (`numpy.float32`). Those misses are
    not silent: EntryBatch refuses and the refused-tick scan raises.
    Imports `number_ok` (one owner) for the overlapping int/float case.
    Nit not Major (no silent disagree besides named `None`/`''`).

## Items checked, not defective under this lens

- **Cycle-6 composition PASS:** re-proved above. `DevelopmentReplay.run`
  → `ReplayAdapter.replay` → `EquityReplay.run` → `bundles_for` →
  `ServeLoop.run`. Fills go through `LegPipeline`.
- **`dskit/production`:** zero-byte diff on `d39e94c..HEAD`. Gate 5a (no
  generic hook). Correct.
- **`path.csv`:** repo-root and child, 0 bytes diff. Agents did not edit
  Path.
- **ADR-0117 Status:** remains `proposed (2026-09-10)` at product and
  HEAD. Cycle-7 did **not** flip Status and did **not** edit Files.
- **Files vs runtime (overlays):** Files names `bundles_for(..., tape=BarTape)`,
  cadence/`Cadence` overlay, equity decider, `ReleaseIdSource`,
  `PaperVenue` around `PaperExecutor` on compose's clock, `rung=shadow`
  because `fsync: none` is shadow-only, `LegPipeline`, no `ServeLoop`
  subclass, no production hook, `HorizonBook` as `(symbol, lead)`.
  Runtime matches those sentences. `snapshot.positions` is `()` by that
  named split.
- **Skip-halt quotes:** child-owned feed policy on the named proposer
  overlay. `Tick.quotes` still builds `QuoteSet`. Empty sets are
  compose-legal. Halt `open=None` still fills the peer at 21.0.
- **Failed-tick raise / refused-tick raise:** wrap `ServeLoop`'s
  already-recorded `status`; do not replace `Tick.run`. Injected
  `RuntimeError` / `ProductionError` prove the scan reads compose's chain.
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
- **Ingest before compose on Python nan:** fail-closed child tape policy
  in the loop that already validated halt flags and unique asof — not a
  parallel ServeLoop.

## What this lens did not review

Fill-timing arithmetic, `forced_exit_price_field` vs recorded exit
price, fee cents, override/queue fill-drop behavior, halt/expiry edge
cases as method correctness — reviewer 1, method lens (`831a292`, PASS
0C 0M 4m). This review used those tests only as evidence of what engine
they exercise (`compose.bundles_for` + `ServeLoop` + `LegPipeline`, not
a private `enumerate` walk and not sixteen stubs). Unused-field /
`float32` ingest gaps are graded here only as duplication-that-diverges
(fail-loud), not as fill-price correctness.

## Handoff

PASS for this lens. Cycle-6's composition PASS still holds after the
cycle-7 ingest refuse: compose is the root, `_TapeCadence` is a
`Cadence`, the sixteen safety stubs are gone, and the safety spine
passed to `ServeLoop` is compose's. `_refuse_non_finite_price` fail-loud
gates before `ServeLoop` for named-field Python/Decimal nan-inf; it does
not silently disagree with EntryBatch besides named halt-skip
`None`/`''`. Cycle-7's refused-tick scan is still a post-run fail-loud
read of compose's `JsonlLedger`, not child-owned recovery. Do not read
this as a durable series (scratch `rmtree`; Phase 5 items 3–5 unbuilt;
ADR-0117 still **proposed**). Keep Status `proposed` until the owner
accepts overlap/suffix/queue/override. Optional follow-ups are the Nits
(route tick-status through `LedgerHistory.ticks`, import or construct
production JSON values at ingest so `float32`/unused fields are one
rule, honest cadence `uses`, distinct release digests). Do not flip ADR
Status; do not edit `path.csv`; do not add a production cadence kind.

**Verbatim verdict:** `PASS — 0 Critical, 0 Major, 12 Nit.`
