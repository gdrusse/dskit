## TL;DR

**FAIL — 1 Critical, 1 Major, 3 Minor.** Commit `abdb71f` does what it
claims for the three cycle-2 holes it named: 2026 bars refuse, non-bool
halt refuses, duplicate `asof_ms` refuses. The new bound is still wrong
for this book: one UTC calendar day cannot close last-day lots at the
child's own 390/780/1170 anchors, and a Friday `evidence_end` admits
Saturday prints while refusing Monday. `json.load` true works; `np.True_`
does not. Leap/midnight UTC `>=` are exact. No product code was changed.

## Review contract

Reviewer 1 of 2, method / API / correctness / replay-parity lens.
Independent of the cycle-3 author. Fresh context. Sequential-mode
re-review after `abdb71f` (`e191b75..abdb71f`). Cycle-2 method FAIL:
`docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method-cycle2.md`
on `358cba6`. Owner rulings unchanged: mixed-horizon overlap,
config-driven next-bar-open, development-only caps, ADR-0117 proposed.

I re-proved cycle-2 C1/M1/M2 and tried to break the new bound: exclusive
midnight ±1 ms, fill-end instant, leap day, winter 19:00 ET, Friday
evidence_end, RTH-390 vs SIP-960 vs dense-1440 suffixes, lead=1/390/1170,
JSON vs numpy halt, duplicate int/float asof, leftover knobs.

## Commands

```
cd children/intraday_equities
python3 -m pytest tests/test_replay.py -q --tb=short
# 23 passed in 0.26s

python3 /tmp/gate5_cycle3_skeptic_repro.py
# proofs below
```

Passing `test_replay.py` (23) still does not cover the bound against
this child's horizons, a Friday `evidence_end`, or halt *types other
than int 1*. The renamed bounds test pins lead=1, a 2026 refuse, and a
fill-only *entry* — not the exit, not `fill_end`, not 390/1170.

## Cycle-2 re-test

| # | Cycle-2 | Cycle-3 (`abdb71f`) |
|---|---|---|
| C1 | unbounded fill tape; 2026 prices; no refuse | **Closed for 2026.** `late_bars` at `evidence_end+2` UTC midnights. `C1_2026_REFUSED True`. Last-day h1 still fills/exits on 2025-10-17 (`HAPPY_LAST_DAY` entry 11.0 @ 1760659200000, exit 12.0 @ +60s). **The calendar proxy that replaced "unbounded" is Critical 1.** |
| M1 | `== True` fail-opens `2`/`'true'`/`None`; pins `1` as halt | **Closed for those values.** `1`/`1.0`/`2`/`'true'`/`None`/`0` all `ConfigError`. `True` skips, `False` trades. `json.loads('true')` skips. **`np.True_` now refuses — Major 1.** |
| M2 | dup `asof_ms`: h1 fill+exit same wall time | **Closed.** `duplicate asof_ms on 'AAA'`. Int/float `1000` also refuses. Cross-symbol same stamp still allowed (correct). |
| Minors | sticker knobs; halt skip+refuse; crash-not-refuse | **Still open** (Minor 1–3). |

## Findings

### Critical 1 — fill-only suffix is +1 UTC calendar day, not the lead/session suffix the book needs

Cycle-2 asked to refuse bars after `evidence_end` except a suffix
*required by already-accepted decisions* (next-bar fill, then `lead`
exit bars). Cycle-3 shipped `_fill_only_end_ms = evidence_end + 2` UTC
midnights: one calendar day of trailing stamps, independent of `lead`,
session, weekend, or holiday.

Universe (`configs/universe.json`): `lead_stop=1170`, anchors
`[390, 780, 1170]`, notes "3 RTH days in 5-minute leads". RTH is 390
one-minute bars. ADR-0117's whole point is mixed-horizon lots. The
bounds test uses `lead=1` only.

Proved (`/tmp/gate5_cycle3_skeptic_repro.py`), `evidence_end=2025-10-16`
(Thu), last RTH minute `2025-10-16T19:59:00Z` (15:59 ET):

```
LEAD390_KINDS ['entry'] refused [..., reason='expiry_past_tape']
# 390 Friday RTH bars are live (last 19:59Z Fri < fill_end Sat 00:00Z)
# fill_index=1, expiry=391, len=391 → unclosed after the entry

LEAD1170_SIP_FILLS ['entry'] refused ['expiry_past_tape']
# 960 SIP Friday minutes (04:00–19:59 ET) still < 1171 bars needed

MONDAY_BARS_REFUSED True
# adding Mon 2025-10-20 13:30Z (the next session that could close h1170)
# → ConfigError fill-only end 1760745600000

LEAD1170_DENSE_FILLS ['entry', 'exit'] refused []
# 1440 consecutive UTC minutes on Friday *does* close — so the bound is
# "next UTC day of wall minutes", not "lead exit bars on the tape"
```

Same root, Friday `evidence_end='2025-10-17'`: fill-only end is Sunday
00:00Z. Monday session bars refuse. Saturday 12:00Z bars *are* inside
the window and become the next-bar fill:

```
FRIDAY_EVIDENCE_MONDAY_BARS True   # ConfigError, 2 bars past Sun 00:00Z
FRIDAY_SAT_FILLS [('entry', 1760788800000, 11.0), ('exit', 1760788860000, 12.0)]
# last-Friday h1 fills and exits on Saturday prints
```

Shipped `run-development-replay.json` is Thursday so fill-only is a
Friday session — h1 works, h390+ last-bar lots still unclose. The node
accepts any `YYYY-MM-DD`; Friday/holiday-eve evidence_end makes the
suffix a non-session day.

Midnight UTC `>=` is *not* off-by-one for the stated UTC date:
`EXCL=1760659200000`, `FILL_END=1760745600000`, leap
`2024-02-29` → Mar 1 / Mar 2, `timestamp()*1000` agrees with `timegm`
on these exact-second midnights. Decision at `exclusive-1ms` allowed;
bar at `fill_end` refused; bar at `fill_end-1` allowed. 20:00 ET last
day *is* exclusive (UTC date, not NY session) — RTH close 16:00 ET
stays on the UTC date; ETH end stamps do not.

**Fix:** size the trailing window to `fill_bar_offset + lead` *session
bars* already accepted, or refuse last-day decisions whose fill/expiry
would leave the window. Do not use a calendar day as a proxy for a
bar-index book, and do not prefer Saturday over Monday. Pin 390/1170
and a Friday `evidence_end` in tests. The 2026 refuse stays.

### Major 1 — `isinstance(..., bool)` rejects `np.True_`; JSON true is fine

Missing halt field still raises. `1`/`1.0`/`2`/`'true'`/`None`/`0` now
refuse (cycle-2 M1 closed). Stdlib `json.loads('true')` skips as a halt.
`isinstance(np.bool_(True), bool)` is False:

```
HALT_json_true     RUN filled@2000=False skipped=True
HALT_np_bool_True  REFUSE  got np.True_ at asof_ms=2000
HALT_np_bool_False REFUSE  got np.False_
```

Cycle-2 proved `np.bool_(True)` *did* skip under `==`. Cycle-3
fail-closes the type this repo's array path actually carries. Error
text says "JSON bool"; `ReplayAdapter` consumes Python bar dicts, not
`json.load`. pandas was unrun (not installed).

**Fix:** accept a named boolean codec (`{True, False}` plus
`np.generic` bools, or an explicit `{0,1}`). Do not claim JSON while
checking `isinstance(bool)` on tape rows.

## Minor

### Minor 1 — leftover sticker knobs (cycle-2 Minor 1, still)

`forced_exit_price_field="close"` exits at 12.5 (live, still unpinned).
`decision_price_field="high"` with `high=99` still fills at 11.0 / 12.0.
`policy.forced_exit_at` / `policy.mark_source` still vocab-only.

### Minor 2 — halt-to-end-of-tape still skip+refuse (cycle-2 Minor 2)

Halt on the expiry bar with no later bar: skipped `halted` @ 3000 **and**
refused `expiry_past_tape` @ 3000, fills=`['entry']`.

### Minor 3 — crash-not-refuse on tape types (cycle-2 Minor 3)

`clock.time.set(np.int64(1000))` → `ProductionError`. `qty="10"` →
`TypeError`. Missing `open` on the fill bar → `KeyError`. The new
`int(asof_ms)` bound check *accepts* numpy integers, then the clock
crashes — a path through the cycle-3 bound into the old hole.

## Not defects (tried)

- Cycle-2 C1 2026 fill: refused by name. Last-day h1 fill-only round-trip
  works when the trailing stamps fit.
- Cycle-2 M1 fail-open on `2`/`'true'`/`None`: now refuse.
- Cycle-2 M2 duplicate asof same-tick fill/exit: now refuse. Two names
  at one stamp still fill.
- Leap day `2024-02-29`: exclusive Mar 1 00:00Z, fill-end Mar 2 00:00Z.
- UTC midnight `>=` exclusive/fill_end: no 1 ms leak on these dates.
- `json.loads('true'|'false')` halt flags behave as Python bools.
- Unknown symbol / off-tape / lead<=0 / unclosed named refusals still
  fire. Halt tick still does not force-exit.
- ADR-0117 status remains **proposed**. Notes still say not ServeLoop.

## Unrun / unknown

- Full repo suite. P16 / market store (document still forbids a market
  read from this file existing). pandas `NA` / `BooleanDtype` halt.
  Real SIP Friday minute counts vs the 960 synthetic. TDD red-first not
  evidenced: tests and fix landed in one commit (`abdb71f`).

## Reproducibility / handoff

Independent method FAIL on `abdb71f`. Cycle-2 C1/M1/M2 as *stated* are
closed; the bound that replaced C1 is the cycle-3 correctness hole.
Do not treat +2 UTC midnights as a mixed-horizon suffix. Do not self-accept
ADR-0117.
