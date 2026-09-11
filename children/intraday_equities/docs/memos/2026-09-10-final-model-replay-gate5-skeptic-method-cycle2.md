## TL;DR

**FAIL — 1 Critical, 2 Major, 3 Minor.** Commit `358cba6` closed the
round-1 silences (unknown symbol, off-tape, unclosed lot, lead<=0) with
named refusals, and it does skip the halt tick. The cycle-2 *bound* fix
deletes the bar-level `evidence_end` check, so a last-day decision fills
and exits on whatever later tape you pass — including 2026 prices — with
no refuse/skip that the bound was exceeded. Halt compare is still not
fail-closed on type. Duplicate `asof_ms` still makes h1 fill and exit at
the same wall time, exit sorted first. ADR-0117 stays proposed. No
product code was changed.

## Review contract

Reviewer 1 of 2, method / API / correctness / replay-parity lens.
Independent of the cycle-2 author. Fresh context. Sequential-mode
re-review after `358cba6` (`1b63628..358cba6`). Round-1 method FAIL:
`docs/memos/2026-09-10-final-model-replay-gate5-skeptic-method.md` on
`1b63628`. First implementation `c286897`. Kickoff read from
`origin/claude/dskit-merge-reviewed-branches-de0z8z`. Owner rulings
unchanged: mixed-horizon overlap, config-driven next-bar-open,
development-only caps, ADR-0117 proposed.

I re-proved every round-1 method item and tried to break the fix:
`<=` expiry, halt catch-up bar, skip+refuse, `1==True`, ServeLoop
claims, fill-policy knobs, evidence trailing bars, lead, numpy asof,
duplicate asof, qty/side crashes.

## Commands

```
cd children/intraday_equities
python3 -m pytest tests/test_replay.py tests/test_nodes_capital.py \
  tests/test_configs.py::test_run_development_replay_forces_ineligible_caps_and_pins_fill_policy -q --tb=short
# 82 passed in 0.97s

python3 -m dskit.pipeline validate configs/run-development-replay.json --adapter intraday_equities
# OK  hash fa648c6b04b289f8533337312caf29efcc33aee3e78fdf709b03b3fbb39411f4

python3 -m dskit.pipeline plan configs/run-development-replay.json --adapter intraday_equities
# same hash; inputs {}; order [replay]

python3 /tmp/gate5_cycle2_skeptic_repro.py
# proofs below
```

Passing `test_replay.py` (22) is still not coverage of the bound or of
halt *types*. `test_development_replay_refuses_bars_after_evidence_end`
now *allows* post-evidence bars and does not assert on the exit.

## Round-1 re-test (short)

| # | Round-1 | Cycle-2 |
|---|---|---|
| 1 | unknown-symbol / off-tape silent | **Fixed.** `unknown_symbol`, `fill_bar_past_tape`. `SILENT_ORPHAN False`, `SILENT_OFF_TAPE False`. |
| 2 | expiry past last bar, lot discarded | **Silence fixed.** `expiry_past_tape` refuse. Entry still has no exit fill (`UNCLOSED_NO_EXIT_STILL True`) — the refuse-the-lot alternative, not a flush. |
| 3 | halt force-exits; `is` fail-open | **Halt tick skip fixed** (`EXIT_ON_HALT_BAR False`). Missing halt field raises. **Type compare not fail-closed — Major 1.** |
| 4 | PaperExecutor rubber stamp; one-graph claim | One-graph claim **removed** (notes + ADR consequences). `Ack.fee` still `Decimal(0)`; Schwab still on the adapter float. Notes now say not ServeLoop / not a production account. Architecture owns the remaining-engine Critical unless the code lies — it does not. Method notes the costume (`ReplayClock.time.set`, dummy digest). |
| 5 | fill knobs stickers; tests pin JSON `"open"` | **fill_price_field live** (`close` → 11.5 ≠ 11.0). Overlap / `forced_exit_horizon_basis` / `same_tick_order` / `halt_handling` read. **`decision_price_field` value still dead — Minor 1.** |
| 6 | evidence_end last-day fill | Last-day fill **works**, by deleting the bar check. **That overshoot is Critical 1.** |
| 7 | lead<=0 | **Fixed.** 0, -1, `True`, `False`, `1.0`, `"1"` all `reason=lead`, no fills. |

## Findings

### Critical 1 — `evidence_end` no longer bounds the tape; last-day lots fill and exit on arbitrary future bars with no refuse

Round-1 asked for a fill-only trailing bar (or refuse last-day
decisions). Cycle-2 dropped the bar filter entirely. `DevelopmentReplay.run`
only refuses **decisions** with `asof_ms >= exclusive`
(`replay.py:680-692`). Bars after 2025-10-16 are fully live for fills,
forced exits, and halt catch-up.

The method name still lies: `test_development_replay_refuses_bars_after_evidence_end`
and `run`'s docstring ("Refuse post-evidence bars") describe the
round-1 check that this commit deleted. The test's own fixture
(`last_day`, `fill_only`, `fill_only+60_000`) produces a
**post-evidence exit** at `1760659260000` / 12.0 that the test never
asserts.

Proved (`/tmp/gate5_cycle2_skeptic_repro.py`):

```
EVIDENCE_FILLS [('entry', 1760659200000, 11.0), ('exit', 1760659260000, 12.0)]
POST_EVIDENCE_EXIT True
Y2026_FILLS [('entry', 1767225600000, 99.0), ('exit', 1767225660000, 100.0)]
FILLS_AT_2026 True
LATE_BARS_ONLY_ACCEPTED True   # empty decisions, 2025-10-17 bars, no error
LATE_DECISION_REFUSED True
```

A 2025-10-16 decision plus a gapped tape that next-bar is 2026-01-01
fills at 99.0 and exits at 100.0. No `refused`/`skipped` row names the
bound. Same class as round-1 Critical 1 (tape-boundary fill with no
observability), inverted: extra fill at out-of-evidence prices instead
of a missing fill.

Config notes admit trailing bars may *fill* last-day decisions. They
do not cap how far, and they do not mention trailing *exits*. The
node's job is "evidence-bounded" (`DevelopmentReplay` docstring). The
bound is now a decision-timestamp filter.

**Fix:** refuse bars after `evidence_end` except a bounded fill/expiry
suffix required by already-accepted decisions (next-bar fill, then
`lead` exit bars). Stamp those rows. Or refuse last-day decisions
whose fill/expiry would leave the window. Do not accept an unbounded
future tape. Rename the test to match whatever bound remains.

### Major 1 — halt `== True` still fail-opens unknown types; `1.0` is a halt

Missing field now raises (round-1 hole closed). Compare is
`bar[field] == halted_true` with `halted_true is True` (`replay.py:438-443`).
Python `1 == True` and `1.0 == True`. Cycle-2
`test_integer_halt_flag_is_compared_by_value_not_identity` **pins**
`halted: 1` as skip — the `==` footgun, not a bool codec.

Proved:

```
HALT_FLAG True     filled@2000=False skipped=True
HALT_FLAG 1        filled@2000=False skipped=True
HALT_FLAG 1.0      filled@2000=False skipped=True   # unexpected type → halt
HALT_FLAG 2        filled@2000=True  skipped=False  # FAIL-OPEN, full round-trip
HALT_FLAG 'true'   filled@2000=True  skipped=False
HALT_FLAG '1'      filled@2000=True  skipped=False
HALT_FLAG None     filled@2000=True  skipped=False  # present-but-unknown trades
HALT_FLAG 0        filled@2000=True  skipped=False
```

A halt column of `None` / `"true"` / `2` still trades on that tick
(entry at 2000, exit at 3000). Same class as round-1 Major 1, shifted
off integer `1`. `np.bool_(True)` does skip (proved). pandas NA was
unrun (no pandas here).

**Fix:** require a real bool (or an explicit `{0,1}` codec named on the
policy). Refuse any other type. Do not pin `1 == True`.

### Major 2 — duplicate `asof_ms` on one symbol: h1 fills and exits at the same wall time, exit sorted first

`index_of` last-wins for *decision* lookup (`replay.py:351`); fill index
is `decision_index + offset`, so a duplicate next stamp is two indices.
h1 entry lands on the first `asof=2000` (open 11.0), expiry index is the
second `asof=2000` (open 99.0). Global sort puts `kind=exit` before
`entry` at equal asof (`replay.py:343-345`).

Proved:

```
DUP_ASOF [('exit', 2000, 99.0), ('entry', 2000, 11.0)]
```

Zero wall-time hold, inverted print order, two prices on one timestamp.
No refuse. `<=` vs `==` is not the cause (`==` would still match index
2). The adapter never uniqueness-checks the tape it indexes.

**Fix:** refuse duplicate `(symbol, asof_ms)` (or collapse them). Do not
emit an exit whose `asof_ms` is not strictly after its entry.

## Minor

### Minor 1 — leftover sticker knobs

`fill_price_field="close"` fills at 11.5 (live; cycle-2 test pins it).
`forced_exit_price_field="close"` exits at 12.5 (live, **unpinned** by
tests). `decision_price_field="high"` with `high=99` still fills at 11.0
/ 12.0 — presence check only (`replay.py:374-381`), value never read.
`policy.forced_exit_at` and `policy.mark_source` never appear in the
run path (vocab-only). `mark_source=fill_bar_open` can disagree with
`fill_price_field=close`.

**Fix:** dispatch or drop. Pin `forced_exit_price_field` the same way as
`fill_price_field`.

### Minor 2 — halt catch-up skip inflation; skip+refuse on halt-to-end-of-tape

`<=` did **not** fire early: h2 fill at 2000, halt at 3000 (before
expiry), exit still at 4000 @ 13.0, `skipped=[]`. Catch-up uses the
next non-halt bar (3000 halt → 4000 @ 13.0), matching the *proposed*
ADR-0117 paragraph. Three consecutive halt bars: **3 skip rows**, then
exit at 6000 @ 15.0 (expiry open 12.0 unused). Halt through end of
tape: same lot in `skipped` (`halted` @ 3000) **and** `refused`
(`expiry_past_tape` @ 3000).

Kickoff owner-ruled skip-not-queue / "that bar's open"; catch-up
queues and reprices. That tension stays on the **proposed** ADR
(architecture). Method: the next-non-halt bar is the one they
implemented; the bug here is counting, not a phantom lot.

**Fix:** one skip per lot per halt episode; if the tape ends while due,
refuse *or* skip, not both.

### Minor 3 — crash-not-refuse on tape types; ReplayClock is a setter bag

`clock.time.set(bar["asof_ms"])` → `ProductionError` on `np.int64(1000)`
(`_instant` requires Python `int`). Missing `open` on the fill bar →
`KeyError`. `qty="10"` submits (`Decimal(str(qty))`) then
`TypeError` on `fee * qty`. `side="hold"` → `ProductionError` from
`Proposal`. Cycle-2 swapped `TestClock` for `ReplayClock` and still
pokes `ManualTime.set` each bar — no `ReplayFeed`, no `sleep_until`.
`Ack.fee` remains `0`; row fee is Schwab float. Notes no longer claim
one graph; not scored as a method Major.

**Fix:** coerce/refuse asof/qty/side/price fields the same way as
`lead`. Clock costume is architecture unless someone still claims
ServeLoop composition (they do not).

## Not defects (tried)

- Unknown symbol / off-tape / lead<=0 / unclosed-lot **silence**: named
  refusals fire.
- Halt tick no longer force-exits. Missing halt field raises.
- `<=` on a pre-expiry halt bar does not exit h2 early.
- Halt catch-up does not pick a random bar; it is the next non-halt
  (4000 after 3000; 6000 after 3000-5000). Same-tick re-entry on a
  halted expiry bar is *lost* (skip-not-queue for entries) — matches
  owner-ruled entry skip, pinned by `test_a_halted_symbol_is_skipped_not_queued`.
- `fill_price_field` close vs open is live. Offset-2 still moves the
  fill. Same-lead refuse / different-lead concurrent / exits-then-entries
  still hold without halt.
- No `ServeLoop` import or subclass. Config notes: "Not ServeLoop, not
  a production account." Module docstring matches. Method does not
  re-open the parallel-engine Critical; architecture owns that while
  the notes stay honest. `plan` still `inputs: {}`.
- Hash unchanged after notes edit (`fa648c6b…`).
- `deployment_eligible=true` still refuses. ADR-0117 status remains
  **proposed**.

## Unrun / unknown

- Full repo suite. P16 / market store (document forbids a market read
  from this file existing). pandas `NA` halt. Crash/restart ledger
  identity (still unbuilt; honest). TDD red-first not evidenced: tests
  and fix landed in one commit (`358cba6`).

## Reproducibility / handoff

Independent method FAIL on `358cba6`. Do not treat proposed halt
catch-up as an accepted ruling. Do not self-accept ADR-0117. The
evidence bound is the cycle-2 correctness hole: last-day lots can
clear at 2026 prices with a green suite.
