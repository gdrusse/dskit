## TL;DR

**FAIL — 0 Critical, 1 Major, 3 Minor.** Cycle-5's named hole is
closed on `5a0c89e`: AAA halt `open=None`/"" still fills BBB at
**21.0**, live `open=""` raises, quiet halt `open=""` with no
decisions is an honest empty (ticks `decided`, no hidden `failed`).
Cycle-4 M1–M4 and Friday/Monday / 390 / 1170 / `np.True_` stay
closed. The new failed-tick check watches only `status=="failed"`.
`float`/`numpy`/`Decimal` nan and inf on `open` refuse the
`EntryBatch` (`refused`, not `failed`), leftover is empty, and a
peer fill vanishes into green `{fills:[], skipped:[], refused:[]}`.
No product code was changed.

## Review contract

Reviewer 1 of 2, method / API / correctness / replay-parity /
fill-timing / fees / overlap-book / halt / tape-bounds / failed-tick
lens. Cursor Grok 4.6. Independent of the cycle-6 author. Sequential
mode, cycle 6 after cycle-5 method FAIL at `90ea3ce`
(`docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle5.md`).
Product `5a0c89e`, journal / HEAD `7cb83ed` (A18879) on
`cursor/gate5-replay-3bda`. Kickoff read via `git show` from
`origin/claude/dskit-merge-reviewed-branches-de0z8z`. Owner rulings
unchanged: mixed-horizon overlap, config-driven next-bar-open,
development-only caps, `deployment_eligible=false`, ADR-0117
**proposed** (not treated as accepted). Universe `lead_stop=1170`,
anchors `[390, 780, 1170]`. Architecture / ServeLoop-composition /
ADR-status / trees / `path.csv` are out of scope.

I re-proved cycle-5 Major 1 and cycle-4 M1–M4 with executed
`ReplayAdapter` / `FillPolicy` / `DevelopmentReplay` / `ConfigError`
calls (not by reading tests), then tried: halt skip vs peer
`QuoteSet`, halted expiry with missing `open`, override close-field
exit, two-symbol concurrent leftover, injected `RuntimeError` tick,
ledger `scan(kind="tick")` envelope shape, `Decimal`/`float`/`numpy`
nan and inf `open` (halt and live, with and without a peer), same
tick AAA halt + BBB entry + BBB exit, `quotes()` called twice,
`np.int64` qty, `decision_price_field`, halt-last skip+refuse.

## Commands

```
cd children/intraday_equities
PYTHONPATH=/workspace:/workspace/children/intraday_equities \
  python3 -m pytest tests/test_replay.py -q --tb=short
# 34 passed in 20.80s

python3 /tmp/gate5_cycle6_skeptic_repro.py
python3 /tmp/gate5_cycle6_skeptic_repro2.py
# proofs below
```

Passing `test_replay.py` (34) pins halt `open=None` still filling
BBB, live `open=""` raising, close-field exit **price**, missing
`open`, string qty, override *exits*, and queue-past-tape refuse. It
does not pin `float('nan')` / `inf` on `open`, tick status
`refused`, or a peer fill when `read_entry` poisons `EntryBatch`.

## Cycle-5 re-test

| # | Cycle-5 (`673377e` / `90ea3ce`) | Cycle-6 (`5a0c89e`) |
|---|---|---|
| M1 | AAA halt `open=None`/"" → `InvalidOperation`, Tick `failed`, BBB last-tick fill vanished; quiet `open=""` green empty | **Closed for None/"".** CONTROL and BROKEN both `BBB` entry `(10, 2000, 21.0)`, AAA `skipped halted`, ticks `decided`. Live `open=""` / `None` raise `ConfigError`. Quiet halt `open=""` no decisions: empty success, ticks `decided`, `HID_FAILED False`. |
| C4 M1 | close-field exit price 12.5 | **Still closed.** Exit price 12.5, fee `0.0297075` = `10 * sell_per_share(12.5)`. |
| C4 M2 | missing `open` / `qty="10"` raise | **Still closed.** `'open'`; `qty must be a positive number, got '10'`. |
| C4 M3 | override qty-10 exit | **Still closed.** Exits `(10, 3000, 12.0)` and `(7, 5000, 14.0)`. |
| C4 M4 | queue-at-end `fill_bar_past_tape` | **Still closed.** `refused=[{reason: fill_bar_past_tape, asof: 2000}]`. |
| C1 / numpy halt | Friday→Monday, 390/1170, `np.True_` | **Still closed.** Saturday/Sunday refuse; Monday h1 fills; lead 390 and 1170 close; `np.True_` skips; `np.False_` fills. |
| Minors | sticker `decision_price_field`; halt-last skip+refuse; `np.int64` qty | **Still open** (Minor 1–3). |

## Findings

### Major 1 — Tick `refused` (non-finite `open`) is not `_fault`; last-tick peer fills vanish

**Problem:** Cycle-6 raises after `loop.run()` only when a tick
envelope `body.status == "failed"`, or leftover `_queued` /
`_pending_by_id`, or `_fault`. `read_entry` puts every name's bar at
this asof — including a **halted** bar — into `EntryBatch.outputs`.
A non-finite float/`Decimal` `open` (`nan`, `inf`, `np.nan`,
`Decimal("nan")`) fails `EntryBatch` construction
(`EntryBatch.outputs.records[0].open: non-finite number … refused`).
That is a `ProductionError` **before** `evaluate` / halt-skip
`quotes()`. `Tick._phases` records status **`refused`**, not
`failed`. `_fault` stays `None`, leftover is empty (evaluate never
queued), the failed-tick check does not fire, and `replay()`
returns.

`quotes()` skipping halt only avoids `Decimal(str(open))` on
`None`/"". It does not keep a halted nan bar out of the batch, so a
non-halted peer never gets a `QuoteSet` on that union tick.

Shipped 11/12 tape, qty 10. AAA halt at 2000; BBB live `open=21.0`;
no bar after 2000:

```
CONTROL  (AAA halt open=11.0): fills [('entry','BBB',10,2000,21.0)]
         skipped AAA halted @ 2000
         refused BBB expiry_past_tape
         ticks 1000/2000 decided
HALT_FLOAT_NAN / HALT_FLOAT_INF / HALT_NP_NAN / HALT_DECIMAL_NAN
         fills []  skipped []  refused []
         RAISED False  FAULT None  leftover queued/pending []
         TICKS 2000 refused EntryBatch.outputs.records[0].open non-finite
         failed_check_would_fire False
         BBB_ENTRY_PRESENT False
         EMPTY_SUCCESS True
LIVE_FLOAT_NAN_PEER  (AAA live nan, BBB 21.0): same empty success
LIVE_STR_NAN_PEER    raises ConfigError Quote.bid NaN  (fail-loud)
HALT_STR_NAN_PEER    still fills BBB @ 21.0  (string is not a float)
HALT_NONE_PEER       still fills BBB @ 21.0  (cycle-5 hole stays closed)
```

Quiet live `float('nan')` with no decisions: `fills=skipped=refused=[]`,
tick 2000 `refused`, green return. That is the cycle-5 quiet-empty
shape on **`refused`**, which this fix's scan does not watch.

Injected `RuntimeError` in `quotes()` **does** raise
`ServeLoop tick status failed: RuntimeError: …`. Envelope shape is
`kind=tick` with nested `body.status` / `body.error.class`. The
check works for `failed` and misses `refused`.

**Fix:** raise on tick status `refused` as well as `failed` (this
replay has no honest `refused` tick), or keep halted / non-finite
bars out of `EntryBatch` so a peer can still quote. Pin halt +
`open=float('nan')` still fills BBB at 21.0 **or** raises by name —
not green empty. Do not treat `status=="failed"` as covering
`EntryBatch` money refusal.

## Minor

### Minor 1 — `decision_price_field` still does not change fills (cycle-3/4/5 Minor 1)

`"high"` with `high=99` still fills 11.0 / 12.0. Presence is
checked; the value is not used. Not a misprice of `fill_price_field`
/ `forced_exit_price_field` (those two move prices). Sticker.

### Minor 2 — halt on the last expiry bar is still skip plus refuse (cycle-4/5 Minor 2)

Halt on asof 3000 with no later bar: entry @ 2000, skipped `halted`
@ 3000, refused `expiry_past_tape` @ 3000. Same dual record with
`open=None` on that halt bar (ticks `decided`, no hidden failed).

### Minor 3 — `np.int64` qty refuses; `np.float64` qty and `np.int64` asof fill

`number_ok(np.int64(10))` is False on this numpy
(`isinstance(np.int64(10), int)` is False). `ReplayAdapter` raises
`qty must be a positive number, got np.int64(10)`. `np.float64(10.0)`
fills. `np.int64` asof fills. Halt already special-cases numpy
bools. Fail-loud, not a silent drop.

## Not defects (tried)

- Cycle-5 M1 None/"": AAA halt `open=None` and `open=""` both fill
  BBB `(entry, 10, 2000, 21.0)`; AAA skipped `halted`; quotes at
  2000 are BBB `21.0` only (not AAA 10/11, not BBB's prior 20.0).
  Live `open=""` → `ConfigError` `could not convert string to float: ''`
  (tick 2000 `failed`, then raised — not green empty). Live
  `open=None` → `float() … NoneType`. Quiet halt `open=""` no
  decisions: empty success, ticks `decided`, `HID_FAILED False`.
- Cycle-4 M1: `forced_exit_price_field="close"` on 11/12 open/close
  tape, qty 10. Entry 11.0 fee `0.0242` = `10 * buy_per_share(11.0)`.
  Exit **price 12.5**, fee `0.0297075` = `10 * sell_per_share(12.5)`,
  not `0.0285972` at 12.0. Override flatten with close-field: flatten
  12.5, new entry 12.0, later exit 14.5.
- Cycle-4 M2: missing `open` → `ConfigError` `'open'`. `qty="10"` →
  `ConfigError` at asof 2000.
- Cycle-4 M3: override entries (10 @ 2000, 7 @ 3000) and exits
  (10 @ 3000, 7 @ 5000).
- Cycle-4 M4: queue last bar refuses `fill_bar_past_tape`.
- Cycle-3 C1: Saturday `1760788800000` weekend refuse; Sunday
  refuse; Monday h1 entry `1760967000000` (13:30Z), exit
  `1760967060000`. Lead 390: entry Monday 13:30Z, exit Monday 20:00Z
  (`1760990400000`). Dense lead 1170: exit Tuesday 09:00Z
  (`1761037200000`). RTH-shaped 1170 (390+390+390+1 = 1171 suffix):
  exit Thursday 13:30Z (`1761226200000`). 1172nd suffix bar refuses
  `fill_suffix_bars=1171`.
- Cycle-3 M1: `np.True_` skips; `np.False_` fills.
- Halt True with valid open: skip, no fill. Halted expiry with
  `open=None`: skip @ 3000, no fill at the missing quote, delayed
  exit @ 4000 **13.0**.
- Two-symbol concurrent: AAA 11.0 / BBB 21.0 entries and 12.0 / 22.0
  exits; leftover `_queued=[]` `_pending_by_id=[]`; ticks `decided`.
  No leftover false-positive on the happy path.
- Same tick: AAA halt @ 2000 + BBB exit 10 @ 21.0 + BBB entry 7 @
  21.0; AAA skipped; quotes at 2000 BBB `21.0` only.
- `quotes()` called twice per tick (phase + `proposals()`): 6 calls
  on a 3-tick tape; fills still 11.0 then 12.0. Double `on_quote` OK.
- Injected `RuntimeError` in `quotes()` at 2000, with or without
  decisions: `ConfigError` `ServeLoop tick status failed:
  RuntimeError: injected failed tick`. Envelope has `body`.
- Both price fields `"close"`: entry 11.5 / exit 12.5.
- Identity: run-document hash
  `5adac27edf7034bc53b3eb6d515bf161aace43d2f027850016e4df0eea6fdd4e`
  matches. Fill-policy digest
  `696b1bcff532e0daeb3396c574021f87fc6cd6cda070827fc016231ffddc6b46`
  matches the pin. `fill_suffix_bars+1` →
  `0409ffa7b33c2f746eb54d2da83c10774ef85d56665c6fdbbdfc8dff9569be46`.
  `fill_suffix_weekdays+1` →
  `75aa134356ccf18fe35e8b7fda933e388adc615a7747904a9c00a65f3835892b`.
  Notes do not move either hash. ADR-0117 status remains **proposed**.

## Math

Let \(P_o, P_c\) be the expiry bar's open and close, qty \(q=10\).
Schwab sell-per-share \(s(P)\) from the shipped rates
(`spread_bps=2.2`, `taf_per_share=0.000195`, `sec31_bps=0.0206`):

\[
s(P) = 2.2 \times 10^{-4}\,P + 0.000195 + 0.0206 \times 10^{-4}\,P
\]

\[
q \cdot s(12.0) = 0.0285972,\quad q \cdot s(12.5) = 0.0297075
\]

Under `forced_exit_price_field="close"`, recorded exit price is
\(P_c=12.5\) and the fee is \(q \cdot s(12.5)\). Cycle-4's split
is still gone.

Peer-name drop: control BBB entry at \(P=21.0\) would pay
\(q \cdot 2.2 \times 10^{-4} \times 21 = 0.0462\). The broken
halt-nan last-tick path records **no** BBB fill (zero notional, not
a 21.0 print), and no skip/refuse row naming BBB.

Fill-only size: `fill_suffix_bars = fill_bar_offset + 1170 = 1 + 1170
= 1171`. Last Friday RTH-only 1170 needs 390+390+390+1 Thursday open =
1171 suffix bars over 4 weekdays; `fill_suffix_weekdays=5` admits that.
The 1172nd bar is refused. That bound held.

## Unrun / unknown

Full repo suite. pandas `NA` / `BooleanDtype` (pandas not installed).
Real SIP Friday minute counts vs the 960 synthetic. Holiday UTC
weekdays vs `universe.json` holidays. Whether every production
`refused` tick is a tape fault this adapter should raise (this
replay currently has no honest `refused`). TDD red-first for
`5a0c89e` not evidenced here (tests and fix landed together).

## Reproducibility / handoff

Independent method FAIL on `5a0c89e` / `7cb83ed`. Cycle-5 None/""
and cycle-4 M1–M4 are closed by executed 21.0 / 12.5 / `ConfigError`
/ qty-10 exit / `fill_bar_past_tape` proofs, not by the 34 passing
tests. Do not treat those tests as coverage of `EntryBatch`
non-finite `open`, tick status `refused`, or halt+`float('nan')`
peer fills. Do not self-accept ADR-0117. Architecture lens is
reviewer 2.
