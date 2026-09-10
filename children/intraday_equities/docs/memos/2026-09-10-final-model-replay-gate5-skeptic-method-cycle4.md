## TL;DR

**FAIL — 0 Critical, 4 Major, 2 Minor.** Cycle-3's two named holes are
closed on `a564a8a`: Friday `evidence_end` now refuses Saturday/Sunday
and fills Monday, last-day lead 390 and 1170 close inside
`fill_suffix_bars=1171` / `fill_suffix_weekdays=5`, and `np.True_` skips.
ServeLoop is on the fill path (1 `run`, 2 paper submits). The compose
also broke a previously-live price knob, swallowed `KeyError`/`TypeError`
into a green empty replay, and the new override/queue dispatches drop
lots or decisions with no matching fill. No product code was changed.

## Review contract

Reviewer 1 of 2, method / API / correctness / replay-parity lens.
Independent of the cycle-4 author. Sequential mode, cycle 4 after
cycle-3 method FAIL at `abdb71f`
(`docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle3.md`).
Product `a564a8a`, journal `f9d6e05`, HEAD `f9d6e05` on
`cursor/gate5-replay-3bda`. Kickoff read via `git show` from
`origin/claude/dskit-merge-reviewed-branches-de0z8z`. Owner rulings
unchanged: mixed-horizon overlap, config-driven next-bar-open,
development-only caps, `deployment_eligible=false`, ADR-0117 proposed
(not treated as accepted). Universe `lead_stop=1170`, anchors
`[390, 780, 1170]`. Architecture / ServeLoop-composition / ADR-status /
trees / `path.csv` are out of scope.

I re-proved cycle-3 C1/M1 with executed code, then tried: leap day,
exclusive midnight ±1 ms, fill-suffix identity movement, last-day lead
390 and 1170 (dense and RTH-shaped), weekend refuse, 2026 refuse, halt
queue/skip, same-lead override vs refuse, duplicate asof, integer halt,
ServeLoop tick fills, numpy int64 asof, and
`decision_price_field` / `mark_source` / `forced_exit_at` /
`forced_exit_price_field` against live fills.

## Commands

```
cd children/intraday_equities
PYTHONPATH=/workspace:/workspace/children/intraday_equities \
  python3 -m pytest tests/test_replay.py -q --tb=short
# 29 passed in 18.53s

python3 /tmp/gate5_cycle4_skeptic_repro.py
python3 /tmp/gate5_cycle4_skeptic_repro2.py
# proofs below
```

Passing `test_replay.py` (29) pins Friday→Monday lead 390, numpy halt,
override *entries*, halt queue retry, and that `forced_exit_at` /
`mark_source` are the shipped strings. It does not pin exit *price*
under `forced_exit_price_field="close"`, a missing `open`, `qty="10"`,
queue-at-end-of-tape, or override *exits*.

## Cycle-3 re-test

| # | Cycle-3 (`abdb71f`) | Cycle-4 (`a564a8a`) |
|---|---|---|
| C1 | +1 UTC calendar day; Friday admits Saturday, refuses Monday; last-day 390/1170 unclose | **Closed.** Saturday/Sunday `ConfigError` weekend refuse. Monday h1 fills. Lead 390 and 1170 close. 1172nd suffix bar refuses. 2026 refuses via `fill_suffix_weekdays=5`. |
| M1 | `isinstance(..., bool)` refuses `np.True_` | **Closed.** `np.True_` skips; `np.False_` fills. `1`/`0`/`1.0`/`'true'`/`None` still refuse. |
| Minors | sticker knobs; halt skip+refuse; crash-not-refuse | **Split.** `forced_exit_price_field` price is now wrong (Major 1). Tape-shape crashes became green empty/misleading refuse (Major 2). `decision_price_field` still dead (Minor 1). Halt-last skip+refuse still (Minor 2). numpy int64 asof now fills. |

## Findings

### Major 1 — `forced_exit_price_field` moves the fee, not the recorded exit price

**Problem:** Cycle-3 measured this knob as live (`"close"` exited at
12.5). After ServeLoop compose, quotes for the paper venue always read
`fill_price_field` (`"open"`). The recorded fill price is
`ack.avg_price` from that quote. The Schwab fee still uses
`forced_exit_price_field`. One named fill-model field now prices the
trade at 12.0 and charges as if it were 12.5.

Shipped bars: open 11.0 / close 11.5 at the fill, open 12.0 / close 12.5
at expiry, qty 10.

```
BASE  exit price 12.0  fee 0.0285972   # sell_per_share(12.0)*10
FX close-field
      exit price 12.0  fee 0.0297075   # sell_per_share(12.5)*10
FORCED_EXIT_PRICE_AFFECTS_EXIT False
FORCED_EXIT_FEE_AFFECTS True
```

`fill_price_field="close"` still moves both legs (11.5 / 12.5) because
that field feeds `quotes()`. `mark_source` / `forced_exit_at` refuse any
other vocab member at load — they are not this hole. ServeLoop did
submit: 2 `PaperExecutor.submit` calls, acks `('filled', '11', '0')` and
`('filled', '12', '0')`; adapter Schwab fees sit on the row beside
ack.fee 0.

**Fix:** quote and ack the exit from `forced_exit_price_field` (or drop
the field if exit is always `fill_price_field`). Pin
`forced_exit_price_field="close"` → exit **price** 12.5, not only the
fee.

### Major 2 — ServeLoop `FAILED` ticks return a successful empty replay

**Problem:** `Tick.run` records a failed tick and does not raise.
`EquityReplay._run_loop` only raises `_fault` (`ConfigError` /
`ProductionError`) or a non-zero loop code. `KeyError` / `TypeError`
never set `_fault`. Loop exits `0` / `stopped`.

Missing `open` (the shipped fill field) on an otherwise ordinary h1
tape:

```
ERR None  LOOP [0, 'stopped']  FAULT None
OUT {'fills': [], 'skipped': [], 'refused': []}
TICKS all three KeyError 'open'
```

That is the cycle-1 silent-orphan shape: no fill, no skip, no refuse,
green return. Cycle-3 crashed with `KeyError`. The compose made it look
like the strategy did not trade.

`qty="10"` (string): ticks 2000 and 3000 `TypeError` ("can't multiply
sequence by non-int of type 'float'"), loop still `0`. Lot opens, no
entry fill, then `expiry_past_tape` with `qty='10'`. Integer halt still
refuses **before** the loop (`_halt_flag`). Float qty `10.0` fills.

**Fix:** if any tick status is `failed`, raise. Refuse non-bool/non-number
qty and missing price fields by name, the way halt already does.

### Major 3 — `same_lead_overlap="override"` leaves an unmatched entry

Shipped JSON is `"refuse"` (second h2 → `same_lead_open`, one entry qty
10 and its exit). Override is a closed-vocab dispatch the author tested
for *entries only*. Executed fills:

```
('entry', qty=10, asof=2000, price=12.0)
('entry', qty=7,  asof=3000, price=13.0)
('exit',  qty=7,  asof=5000, price=15.0)
OVERRIDE_EXIT_FOR_QTY10 False
```

`open_lot` pops the first lot with no exit row. The book then expires
only qty 7. Two buys, one sell. ADR-0117's proposed rule is refuse, not
override; the code still offers override and emits an un-closable fill.

**Fix:** on override, force-exit the open lot at this bar's fill price
(or drop override from `_VOCAB` until the owner rules it). Pin that the
qty-10 entry has a qty-10 exit.

### Major 4 — `halt_handling="queue"` on the last bar drops the decision

Default `"skip"` on a halted fill bar: `fills=[]`,
`skipped=[{reason: halted, asof: 2000}]`. Queue with a later live bar
retries: entry 3000 @ 12.0, exit 4000 @ 13.0. Queue when the halted fill
bar **is** the last bar:

```
QUEUE_END_OF_TAPE fills [] skipped [] refused []
```

`pending[index+1]` is never visited. A valid h1 decision on a two-bar
tape vanishes. Skip at least writes a skip row. Queue is a documented
vocab member.

**Fix:** if the queued index is past the tape, refuse or skip by name
(`fill_bar_past_tape` / `halted`).

## Minor

### Minor 1 — `decision_price_field` still does not change fills (cycle-3 Minor 1)

`"high"` with `high=99` still fills 11.0 / 12.0, same as `"close"`.
Presence is checked; the value is not used. `mark_source` and
`forced_exit_at` other members now refuse at policy load (cycle-3
vocab-only complaint on those two is closed).

### Minor 2 — halt on the last expiry bar is still skip plus refuse (cycle-3 Minor 2)

Halt on asof 3000 with no later bar: entry @ 2000, skipped `halted` @
3000, refused `expiry_past_tape` @ 3000. Same dual record.

## Not defects (tried)

- Cycle-3 C1: Saturday `1760788800000` weekend refuse; Sunday refuse;
  Monday h1 `entry 1760967000000 @ 11.0`, `exit 1760967060000 @ 12.0`.
  Last-day lead 390: entry Monday 13:30Z, exit Monday 20:00Z
  (`1760990400000`), 13.1 s through ServeLoop. Dense lead 1170: entry
  Monday 13:30Z, exit Tuesday 09:00Z (`1761037200000`). RTH-shaped 1170
  (Mon 390 + Tue 390 + Wed 390 + Thu 1 = 1171 suffix bars): entry
  Monday 13:30Z, exit Thursday 13:30Z (`1761226200000`). 1172nd suffix
  bar refuses `fill_suffix_bars=1171`. SIP-like 960 Friday minutes after
  Thursday evidence_end: entry only, `expiry_past_tape` (not enough
  bars — correct). 2026-01-01 and the 6th weekday
  (`2025-10-27`) refuse `fill_suffix_weekdays=5`.
- Cycle-3 M1: `np.True_` / `np.bool_(True)` skip; `np.False_` fills;
  JSON `true`/`false` match; `1`/`0`/`1.0`/`'true'`/`None` refuse.
- Leap `2024-02-29`: exclusive `1709251200000` (2024-03-01 00:00Z)
  matches `timegm`. Thursday exclusive `1760659200000` matches.
  Decision at exclusive−1 ms allowed; decision at exclusive refused;
  bar at exclusive is fill-only and fills.
- Identity: digest `696b1bcff532e0daeb3396c574021f87fc6cd6cda070827fc016231ffddc6b46`
  matches `run-development-replay.json`. `fill_suffix_bars+1` →
  `0409ffa7…`; `fill_suffix_weekdays+1` → `75aa1343…`; notes do not move
  the hash.
- Duplicate asof (int/int and int/float 1000) refuses; two names at one
  stamp still fill. Same-lead default refuse keeps qty 10. numpy int64
  asof fills through the adapter and `DevelopmentReplay`. ServeLoop
  happy path: 3 ticks `decided`, code 0, fills 11.0 then 12.0.
- Integer halt still `ConfigError` before the loop. Unknown symbol /
  off-tape / lead<=0 / unclosed named refusals still fire (suite).
  ADR-0117 status remains **proposed**.

## Math

Let \(P_o, P_c\) be the expiry bar's open and close, qty \(q=10\).
Schwab sell-per-share \(s(P)\) from the shipped rates.

\[
q \cdot s(12.0) = 0.0285972,\quad q \cdot s(12.5) = 0.0297075
\]

Recorded exit price under `forced_exit_price_field="close"` is still
\(P_o=12.0\), not \(P_c=12.5\). The fee uses \(s(P_c)\). A ledger that
adds price and fee therefore marks the exit at one print and costs it
at another.

Fill-only size: `fill_suffix_bars = fill_bar_offset + 1170 = 1 + 1170
= 1171`. Last Friday RTH-only 1170 needs 390+390+390+1 Thursday open =
1171 suffix bars over 4 weekdays; `fill_suffix_weekdays=5` admits that.
The 1172nd bar is refused. That bound is tight and held in this run.

## Unrun / unknown

Full repo suite. pandas `NA` / `BooleanDtype` (pandas not installed).
Real SIP Friday minute counts vs the 960 synthetic. TDD red-first not
evidenced: tests and fix landed in `a564a8a`. Holiday UTC-weekdays vs
`universe.json` holidays. Whether a production serve document (not this
pipeline node) would surface `Tick` `failed` status.

## Reproducibility / handoff

Independent method FAIL on `a564a8a` / `f9d6e05`. Cycle-3 C1 and M1 are
closed by executed Friday/Monday/390/1170/`np.True_` proofs, not by the
commit message. Do not treat 29 passing tests as coverage of exit price,
malformed tape, override exits, or queue-at-end. Do not self-accept
ADR-0117.
