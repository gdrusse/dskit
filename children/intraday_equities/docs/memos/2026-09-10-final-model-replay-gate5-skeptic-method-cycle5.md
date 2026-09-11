## TL;DR

**FAIL — 0 Critical, 1 Major, 3 Minor.** Cycle-4's four named holes are
closed on `673377e`: close-field exits at **12.5**, missing `open` and
`qty="10"` raise `ConfigError`, override emits a qty-10 exit, and
queue-at-end refuses `fill_bar_past_tape`. A halted name with
`open=None`/"" still turns `quotes()` into an uncaught
`InvalidOperation`; Tick records `failed` and the adapter returns success,
dropping a peer name's last-tick fill. No product code was changed.

## Review contract

Reviewer 1 of 2, method / API / correctness / replay-parity /
fill-timing / fees / overlap-book / halt / tape-bounds lens. Cursor
Grok 4.6. Independent of the cycle-5 author. Sequential mode, cycle 5
after cycle-4 method FAIL at `040bd7c`
(`docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle4.md`).
Product `673377e`, journal / HEAD `5f49bfa` (A18877) on
`cursor/gate5-replay-3bda`. Kickoff read via `git show` from
`origin/claude/dskit-merge-reviewed-branches-de0z8z`. Owner rulings
unchanged: mixed-horizon overlap, config-driven next-bar-open,
development-only caps, `deployment_eligible=false`, ADR-0117 **proposed**
(not treated as accepted). Universe `lead_stop=1170`, anchors
`[390, 780, 1170]`. Architecture / ServeLoop-composition / ADR-status /
trees / `path.csv` are out of scope.

I re-proved cycle-4 M1–M4 with executed `ReplayAdapter` / `FillPolicy` /
`DevelopmentReplay` / `ConfigError` calls (not by reading tests), then
tried: Tick `failed` without `_fault`, `ack.avg_price` vs row vs Schwab
fee, `decision_price_field`, halt on last expiry, override+halt,
override then expiry same tick, queue then later halt, queue across a
Fri→Mon gap, both price fields `"close"`, string/float/numpy qty and
asof, missing `forced_exit_price_field` on expiry only, two symbols
with staggered tapes, exclusive midnight ±1 ms, leap day, and identity
movement.

## Commands

```
cd children/intraday_equities
PYTHONPATH=/workspace:/workspace/children/intraday_equities \
  python3 -m pytest tests/test_replay.py -q --tb=short
# 32 passed in 20.34s

python3 /tmp/gate5_cycle5_skeptic_repro.py
python3 /tmp/gate5_cycle5_skeptic_repro2.py
python3 /tmp/gate5_cycle5_skeptic_repro3.py
# proofs below
```

Passing `test_replay.py` (32) pins close-field exit **price**, missing
`open`, string qty, override *exits*, and queue-past-tape refuse. It
does not pin `quotes()` `Decimal(str(open))` on a halted bar, Tick
`failed` without `_fault`, or a last-tick leftover `_queued` fill.

## Cycle-4 re-test

| # | Cycle-4 (`a564a8a` / `040bd7c`) | Cycle-5 (`673377e`) |
|---|---|---|
| M1 | `forced_exit_price_field="close"` recorded price 12.0, fee as if 12.5 | **Closed.** Exit price 12.5, fee `0.0297075` = `10 * sell_per_share(12.5)`. `ack.avg_price='12.5'`. |
| M2 | missing `open` / `qty="10"` → `fills=skipped=refused=[]`, loop 0 | **Closed.** Both raise `ConfigError` (`'open'`; `qty must be a positive number, got '10'`). `_fault` set. |
| M3 | override qty-10 entry, only qty-7 exit | **Closed.** Exits `(10, 3000, 12.0)` and `(7, 5000, 14.0)`. |
| M4 | queue on last halted fill bar vanished | **Closed.** `refused=[{reason: fill_bar_past_tape, asof: 2000}]`. |
| C1 / numpy halt | Friday→Monday, 390/1170, `np.True_` | **Still closed.** Saturday/Sunday refuse; Monday h1 fills; lead 390 and 1170 close; `np.True_` skips; `np.False_` fills. |
| Minors | sticker `decision_price_field`; halt-last skip+refuse | **Still open** (Minor 1, Minor 2). |

## Findings

### Major 1 — Tick `failed` (`InvalidOperation`) is not `_fault`; last-tick peer fills vanish

**Problem:** `Tick.run` records any uncaught exception as `status=failed`
and does not re-raise. `EquityReplay._run_loop` only raises `_fault`
(`ConfigError` / wrapped `KeyError`/`TypeError`/`ValueError`) or a
non-zero loop code. `quotes()` does `Decimal(str(bar[fill_price_field]))`
and does **not** wrap `decimal.InvalidOperation`. A halted bar never
reads the price in `_apply_bar`, so `open=None` or `open=""` skip the
name, then `quotes()` blows up, Tick is `failed`, `_fault` stays
`None`, loop exits `0` / `stopped`, and `replay()` returns.

Evaluate already `open_lot` / `_queue_fill` for other names on that
tick. `_queued` is consumed only in `proposals()`, which does not run
after a failed quotes phase. A later tick can accidentally flush the
leftover (BBB still filled when a 3000 bar existed). When the failed
tick **is the last union tick**, the leftover never submits: no entry
row, lot swept as `expiry_past_tape`.

Shipped 11/12 tape, qty 10. AAA halt at 2000 with `open=None` (or
`""`); BBB live `open=21.0`; no bar after 2000:

```
CONTROL  (AAA halt open=11.0): fills [('entry','BBB',10,2000,21.0)]
         skipped AAA halted @ 2000
         refused BBB expiry_past_tape (tape has no expiry bar)
BROKEN   (AAA halt open=None):  fills []
         skipped AAA halted @ 2000
         refused BBB expiry_past_tape qty=10
         RAISED False  LOOP [0]  FAULT None
TICKS    2000 failed InvalidOperation ConversionSyntax
BBB_ENTRY_PRESENT False
```

Quiet `open=""` with no decisions: `fills=skipped=refused=[]`, same
failed tick, green return. Live `open=None` / `open=""` on a *non-halt*
bar still raise (`TypeError` / `float('')` wrapped) — the hole is the
halt/quiet path that never hits `_process_entries`.

**Fix:** wrap `InvalidOperation` (and the rest of `Exception`) in
`quotes()` / `evaluate` into `ConfigError` + `_fault`; after `loop.run()`,
raise if any tick status is `failed`; refuse leftover `_queued` /
`_pending_by_id` by name at end of tape. Do not parse `fill_price_field`
on a halt-skipped bar.

## Minor

### Minor 1 — `decision_price_field` still does not change fills (cycle-3/4 Minor 1)

`"high"` with `high=99` still fills 11.0 / 12.0. Presence is checked;
the value is not used. Not a misprice of `fill_price_field` /
`forced_exit_price_field` (those two move prices). Sticker.

### Minor 2 — halt on the last expiry bar is still skip plus refuse (cycle-4 Minor 2)

Halt on asof 3000 with no later bar: entry @ 2000, skipped `halted` @
3000, refused `expiry_past_tape` @ 3000. Same dual record.

### Minor 3 — `np.int64` qty refuses; `np.float64` qty and `np.int64` asof fill

`number_ok(np.int64(10))` is False on this numpy (`isinstance(np.int64(10), int)`
is False). `ReplayAdapter` raises `qty must be a positive number, got np.int64(10)`.
`np.float64(10.0)` fills. `np.int64` asof fills. Halt already special-cases
numpy bools. Fail-loud, not a silent drop.

## Not defects (tried)

- Cycle-4 M1: `forced_exit_price_field="close"` on 11/12 open/close tape,
  qty 10. Entry 11.0 fee `0.0242` = `10 * buy_per_share(11.0)`. Exit
  **price 12.5**, fee `0.0297075` = `10 * sell_per_share(12.5)`, not
  `0.0285972` at 12.0. Paper `ack.avg_price='12.5'`, `ack.fee='0'`
  (`paper_fees=none`); Schwab sits on the row. Basis agrees.
- Cycle-4 M2: missing `open` → `ConfigError` `'open'` (evaluate wrap).
  `qty="10"` → `ConfigError` at asof 2000. Live `open=None` raises
  `float() ... NoneType`. Missing `close` on expiry with
  `forced_exit_price_field="close"` → `ConfigError` `'close'`.
- Cycle-4 M3: override entries (10 @ 2000, 7 @ 3000) and exits
  (10 @ 3000, 7 @ 5000). Override+halt skip: qty-7 skipped at 3000, qty-10
  exits at 4000. Queue: qty-7 fills at 4000 after the qty-10 exit.
  Same-tick h1 expiry then new h1: exit 10 @ 3000 then entry 7 @ 3000.
- Cycle-4 M4: queue last bar refuses `fill_bar_past_tape`. Queue retry:
  entry 3000 @ 12.0, exit 4000 @ 13.0. Queue then later halt: fills at
  4000 @ 13.0. Queue then halt at end: refuse at 3000. Adapter queue
  Fri halt → Monday next index: entry `1760967000000` @ 11.0.
- Cycle-3 C1: Saturday `1760788800000` weekend refuse; Sunday
  `1760875200000` refuse; Monday h1 entry `1760967000000` (13:30Z),
  exit `1760967060000`. Lead 390: entry Monday 13:30Z, exit Monday
  20:00Z (`1760990400000`). Dense lead 1170: exit Tuesday 09:00Z
  (`1761037200000`). RTH-shaped 1170 (390+390+390+1 = 1171 suffix):
  exit Thursday 13:30Z (`1761226200000`). 1172nd suffix bar refuses
  `fill_suffix_bars=1171`. 2026-01-01 and the 6th weekday
  (`2025-10-27`) refuse `fill_suffix_weekdays=5`.
- Cycle-3 M1: `np.True_` / `np.bool_(True)` skip; `np.False_` fills;
  integer halt `ConfigError` before the loop; `1`/`0` still refuse.
- Both `fill_price_field` and `forced_exit_price_field` `"close"`:
  entry 11.5 / exit 12.5. Override flatten with close-field: flatten
  12.5, new entry 12.0, later exit 14.5 — ack prices match the named
  fields.
- Two aligned names fill independently. Staggered tapes (BBB missing
  3000, has 4000): AAA exits at 3000, BBB at 4000; all ticks `decided`.
  BBB missing 3000 with no later bar: AAA exits, BBB `expiry_past_tape`
  (BBB's own sequence has no expiry index) — not a vanished AAA fill.
- Leap `2024-02-29`: exclusive `1709251200000` (2024-03-01 00:00Z)
  matches `timegm`. Thursday exclusive `1760659200000` matches.
  Decision at exclusive−1 ms allowed; decision at exclusive refused;
  bar at exclusive is fill-only and fills.
- Identity: run-document hash
  `5adac27edf7034bc53b3eb6d515bf161aace43d2f027850016e4df0eea6fdd4e`
  matches. Fill-policy digest
  `696b1bcff532e0daeb3396c574021f87fc6cd6cda070827fc016231ffddc6b46`
  matches the pin. `fill_suffix_bars+1` →
  `0409ffa7b33c2f746eb54d2da83c10774ef85d56665c6fdbbdfc8dff9569be46`.
  `fill_suffix_weekdays+1` moves. Notes do not move either hash.
  Duplicate asof int/float 1000 refuses. String asof `"1000"` fills
  (`int()`). `qty` 10.0 fills; `True` qty refuses. ADR-0117 status
  remains **proposed**.

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
(price at open, fee at close) is gone.

Peer-name drop: control BBB entry at \(P=21.0\) would pay
\(q \cdot 2.2 \times 10^{-4} \times 21 = 0.0462\). The broken last-tick
path records **no** BBB fill (zero notional, not a 21.0 print).

Fill-only size: `fill_suffix_bars = fill_bar_offset + 1170 = 1 + 1170
= 1171`. Last Friday RTH-only 1170 needs 390+390+390+1 Thursday open =
1171 suffix bars over 4 weekdays; `fill_suffix_weekdays=5` admits that.
The 1172nd bar is refused. That bound held.

## Unrun / unknown

Full repo suite. pandas `NA` / `BooleanDtype` (pandas not installed).
Real SIP Friday minute counts vs the 960 synthetic. Holiday UTC
weekdays vs `universe.json` holidays. Whether leftover `_queued` can
submit on a *refused* (not failed) tick. TDD red-first for `673377e`
not evidenced here (tests and fix landed together).

## Reproducibility / handoff

Independent method FAIL on `673377e` / `5f49bfa`. Cycle-4 M1–M4 are
closed by executed 12.5 / `ConfigError` / qty-10 exit / `fill_bar_past_tape`
proofs, not by the 32 passing tests. Do not treat those tests as
coverage of halted `open=None` `quotes()`, Tick `failed` without
`_fault`, or last-tick leftover `_queued`. Do not self-accept
ADR-0117. Architecture lens is reviewer 2.
