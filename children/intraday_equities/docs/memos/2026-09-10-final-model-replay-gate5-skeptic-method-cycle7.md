## TL;DR

**PASS — 0 Critical, 0 Major, 4 Minor.** Cycle-6's named hole is
closed on `5914052`: halt/live `open=float('nan')`/`inf`/`np.nan`/
`Decimal('nan')` raise `ConfigError` naming **open** (not
`fills=skipped=refused=[]`). Halt `None`/"" still fills BBB at
**21.0**. Quiet live nan with no decisions raises. Tick status
`refused` is fail-loud as well as `failed`. Unused `high`/`low`/
`volume` nan and `np.float32` miss ingest but still raise via
EntryBatch `refused`, not green empty. Cycle-4 M1–M4 and
Friday/Monday / 390 / 1170 / `np.True_` stay closed. No product
code was changed.

## Review contract

Reviewer 1 of 2, method / API / correctness / replay-parity /
fill-timing / fees / overlap-book / halt / tape-bounds /
failed-and-refused-tick lens. Cursor Grok 4.6. Independent of the
cycle-7 author. Sequential mode, cycle 7 after cycle-6 method FAIL
at `5a0c89e`
(`docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle6.md`).
Product `5914052`, journal / HEAD `140e387` (A18881) on
`cursor/gate5-replay-3bda`. Kickoff read via `git show` from
`origin/claude/dskit-merge-reviewed-branches-de0z8z`. Owner rulings
unchanged: mixed-horizon overlap, config-driven next-bar-open,
development-only caps, `deployment_eligible=false`, ADR-0117
**proposed** (not treated as accepted). Universe `lead_stop=1170`,
anchors `[390, 780, 1170]`. Architecture / ServeLoop-composition /
ADR-status / trees / `path.csv` are out of scope.

I re-proved cycle-6 Major 1 and cycle-4 M1–M4 with executed
`ReplayAdapter` / `FillPolicy` / `DevelopmentReplay` / `ConfigError`
/ probed `ServeLoop` tick envelopes (not by reading tests), then
tried: `np.nan` vs `float` nan vs `math.nan` vs `Decimal` nan vs
`±inf` / `np.inf` / `Decimal('±Infinity')`; non-finite **close**
under `forced_exit_price_field="close"`; decision-field-only close
nan; unused `high`/`low`/`volume` nan vs EntryBatch; `np.float32`
nan and finite; two-symbol staggered tape; leftover vs refused
raise order; string `'nan'`/`'inf'` halt; numpy bool halt;
override close-field flatten; leftover queue after live string nan.

## Commands

```
cd children/intraday_equities
PYTHONPATH=/workspace:/workspace/children/intraday_equities \
  python3 -m pytest tests/test_replay.py -q --tb=short
# 35 passed in 18.97s

python3 /tmp/gate5_cycle7_skeptic_repro.py
python3 /tmp/gate5_cycle7_skeptic_repro2.py
# proofs below
```

Passing `test_replay.py` (35) pins halt `open=None` still filling
BBB, live `open=""` raising, `float('nan')`/`inf` on `open` naming
open, close-field exit **price**, missing `open`, string qty,
override *exits*, and queue-past-tape refuse. It does not pin
`np.float32`, unused `high`/`low` nan, staggered-tape tick status,
or leftover-vs-refused order.

## Cycle-6 re-test

| # | Cycle-6 (`5a0c89e` / `7cb83ed`) | Cycle-7 (`5914052`) |
|---|---|---|
| M1 | AAA halt `open=float('nan')`/`inf`/`np.nan`/`Decimal('nan')` → EntryBatch `refused`, leftover empty, green `{fills:[], skipped:[], refused:[]}`; quiet live nan same | **Closed.** All four (plus `-inf`, `np.inf`, `-np.inf`, `math.nan`, `Decimal('±Infinity')`) raise `ConfigError` naming `open` at `asof_ms=2000` before ServeLoop. `EMPTY_SUCCESS False`. `TICKS []`. Quiet live nan with no decisions raises the same, not green empty. |
| C5 M1 | halt `open=None`/"" + BBB fills 21.0 | **Still closed.** CONTROL `BBB` entry `(10, 2000, 21.0)`, AAA `skipped halted`, ticks `decided`. Live `open=""` / `None` raise (`failed` tick then `_fault`). Quiet halt `open=""` no decisions: empty success, ticks `decided`. |
| C4 M1 | close-field exit price 12.5 | **Still closed.** Exit price 12.5, fee `0.0297075` = `10 * sell_per_share(12.5)`. Override flatten with close-field: flatten **12.5**, new entry 12.0, later exit **14.5**. |
| C4 M2 | missing `open` / `qty="10"` raise | **Still closed.** `'open'`; `qty must be a positive number, got '10'`. Missing close on expiry with close-field: `'close'`. |
| C4 M3 | override qty-10 exit | **Still closed.** Exits `(10, 3000, 12.0)` and `(7, 5000, 14.0)`. |
| C4 M4 | queue-at-end `fill_bar_past_tape` | **Still closed.** `refused=[{reason: fill_bar_past_tape, asof: 2000}]`. |
| C1 / numpy halt | Friday→Monday, 390/1170, `np.True_` | **Still closed.** Saturday/Sunday refuse; Monday h1 fills `1760967000000` / `1760967060000`; lead 390 exit `1760990400000`; dense 1170 exit `1761037200000`; RTH 1170 exit `1761226200000`; 1172nd suffix refuses `fill_suffix_bars=1171`. `np.True_` / `np.bool_(True)` skip; `np.False_` fills. |
| Minors | sticker `decision_price_field`; halt-last skip+refuse; `np.int64` qty | **Still open** (Minor 1–3). Unused-field / `float32` ingest gap is Minor 4 (fail-loud). |

## Findings

None at Critical or Major.

## Minor

### Minor 1 — `decision_price_field` still does not change fills (cycle-3/4/5/6 Minor 1)

`"high"` with `high=99` still fills 11.0 / 12.0. Presence is
checked; the value is not used. Not a misprice of `fill_price_field`
/ `forced_exit_price_field` (those two move prices). Sticker.

### Minor 2 — halt on the last expiry bar is still skip plus refuse (cycle-4/5/6 Minor 2)

Halt on asof 3000 with `open=None` and no later bar: entry @ 2000,
skipped `halted` @ 3000, refused `expiry_past_tape` @ 3000. Ticks
`decided`, no hidden failed.

### Minor 3 — `np.int64` qty refuses; `np.float64` qty and `np.int64` asof fill

`ReplayAdapter` raises `qty must be a positive number, got
np.int64(10)`. `np.float64(10.0)` fills. `np.int64` asof fills.
Halt already special-cases numpy bools. Fail-loud, not a silent drop.

### Minor 4 — ingest walks only the three named price fields; unused / `float32` still hit EntryBatch

`_refuse_non_finite_price` runs only on `fill_price_field`,
`forced_exit_price_field`, and `decision_price_field`, and only for
Python `int`/`float`/`Decimal`. Production `EntryBatch` still
refuses **any** record float that is non-finite, and any
`numpy.float32` (not a JSON value). Cycle-7's tick-status
`refused` check then raises — leftover `[]`, not green empty:

```
UNUSED_HIGH_NAN_PEER     raise …records[0].high: non-finite number nan
UNUSED_LOW_NAN_HALT_NONE raise …records[0].low: non-finite number nan
UNUSED_VOLUME_NAN        raise …records[0].volume: non-finite number nan
HALT_TRUE + high=nan     raise …high…  (BBB 21.0 not returned)
FINITE_F32_OPEN          raise …open: float32 is not a JSON value
HALT/LIVE_F32_NAN_OPEN   raise …open: float32 is not a JSON value
CLOSE_NAN when decision=open  raise …close: non-finite number nan
```

A halted name with a valid skippable `open=None`/finite open still
goes into `read_entry` records, so an unused nan on that bar aborts
the peer via raise rather than skip-and-fill. Fail-loud, names the
field, not a silent 21.0 drop. Ingest does not name `high`/`low`/
`volume`/`float32` itself.

## Not defects (tried)

- Cycle-6 M1 nan/inf: halt and live `float('nan')`, `inf`, `-inf`,
  `np.nan`, `np.inf`, `-np.inf`, `math.nan`, `Decimal('NaN')`,
  `Decimal('Infinity')`, `Decimal('-Infinity')` all raise naming
  `open` at ingest. Quiet live nan, no decisions: same raise, not
  `{fills:[], skipped:[], refused:[]}`.
- Cycle-5 M1 None/"": AAA halt `open=None` and `open=""` both fill
  BBB `(entry, 10, 2000, 21.0)` fee `0.0462`; AAA skipped `halted`;
  ticks `1000/2000 decided`. Live `open=""` → `ConfigError`
  `could not convert string to float: ''` (tick 2000 `failed`,
  `_fault` raised — not green empty). Live `open=None` → `float()
  … NoneType`. Quiet halt `open=""` no decisions: empty success,
  ticks `decided`.
- String `'nan'` / `'inf'` halt still fills BBB @ 21.0 (string is
  not a float; cycle-6 not-defect). Live string `'nan'` raises
  `Quote.bid NaN` (`failed`); leftover then holds AAA+BBB queued
  fills but `_fault` raises first — fail-loud, not green empty.
- Cycle-4 M1: `forced_exit_price_field="close"` on 11/12 open/close
  tape, qty 10. Entry 11.0 fee `0.0242`. Exit **price 12.5**, fee
  `0.0297075`. Non-finite close on the expiry bar raises naming
  `close` at ingest (`nan`/`inf`/`np.nan`/`Decimal('NaN')`) — no
  open-price exit print.
- Cycle-4 M2/M3/M4: as the re-test table.
- Cycle-3 C1: Saturday `1760788800000` weekend refuse; Sunday
  `1760875200000` refuse; Monday h1 entry `1760967000000` (13:30Z),
  exit `1760967060000`. Lead 390: entry Monday 13:30Z, exit Monday
  20:00Z (`1760990400000`). Dense lead 1170: exit Tuesday 09:00Z
  (`1761037200000`). RTH-shaped 1170 (390+390+390+1 = 1171 suffix):
  exit Thursday 13:30Z (`1761226200000`). 1172nd suffix bar refuses
  `fill_suffix_bars=1171`.
- Cycle-3 M1: `np.True_` and `np.bool_(True)` skip; `np.False_` fills.
- Two-symbol staggered tape (BBB missing 2000): ticks all `decided`
  (not `refused` / `skipped:no_coverage`). AAA entry 11.0 @ 2000,
  AAA exit 12.0 @ 3000, BBB entry 22.0 @ 3000 (next BBB bar). No
  false-positive refused raise on an honest coverage/skip tick.
  Staggered with no decisions: empty success, ticks `decided`.
- Two-symbol concurrent: AAA 11.0 / BBB 21.0 entries and 12.0 /
  22.0 exits; leftover `_queued=[]`; ticks `decided`.
- Same tick: AAA halt `open=None` @ 2000 + BBB entry 10 @ 21.0 +
  later BBB exit 10 @ 22.0 and entry 7 @ 22.0; AAA skipped; ticks
  `decided`.
- Halt-last `open=None`: skip+refuse dual record, ticks `decided`.
- Both price fields `"close"`: entry 11.5 / exit 12.5.
- Decision-field-only close nan (shipped `decision_price_field=
  "close"`, fill/exit `open`): ingest raises naming `close` — not
  a silent fill at 11.0 / 21.0.
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
is still gone. Peer BBB entry at \(P=21.0\) pays
\(q \cdot 2.2 \times 10^{-4} \times 21 = 0.0462\); halt
`open=None` still records that print.

Fill-only size: `fill_suffix_bars = fill_bar_offset + 1170 = 1 + 1170
= 1171`. Last Friday RTH-only 1170 needs 390+390+390+1 Thursday open =
1171 suffix bars over 4 weekdays; `fill_suffix_weekdays=5` admits that.
The 1172nd bar is refused. That bound held.

## Unrun / unknown

Full repo suite. pandas `NA` / `BooleanDtype` (pandas not installed).
Real SIP Friday minute counts vs the 960 synthetic. Holiday UTC
weekdays vs `universe.json` holidays. Whether a production `refused`
tick other than EntryBatch poison / feed `dead` can appear on this
adapter (staggered honest ticks were `decided`; tape feed status is
always `live`). TDD red-first for `5914052` not evidenced here
(the new test and fix landed together).

## Reproducibility / handoff

Independent method PASS on `5914052` / `140e387`. Cycle-6 nan/inf
green-empty and cycle-4 M1–M4 are closed by executed 21.0 / 12.5 /
`ConfigError` naming `open` / qty-10 exit / `fill_bar_past_tape`
proofs, not by the 35 passing tests. Unused-field nan is fail-loud
via `refused`, not a silent peer drop. Do not self-accept ADR-0117.
Architecture lens is reviewer 2.
