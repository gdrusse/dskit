## TL;DR

**FAIL — 2 Critical, 4 Major, 2 Minor.** Commit `c286897` ships a synthetic
next-bar-open book that the focused suite cannot see fail at the tape
boundary, on halt identity, or on production-ledger fees. ADR-0117 stays
proposed (not treated as accepted here). No product code was changed.

## Review contract

Reviewer 1 of 2, sequential-mode first lens: method / API / correctness /
replay–production parity only. Architecture/tiering is the other agent.

Commit `c286897` on `cursor/gate5-replay-3bda` vs
`origin/claude/phase1-recovery-seven-gates-ao4zdj`. Read: kickoff (fetched
from `origin/claude/dskit-merge-reviewed-branches-de0z8z`), plan §6 Phase 5
and §9 lens 7, ADR-0114 Phase 5, ADR-0117 (proposed), Gate 5a memos (not
re-derived), and the Gate 5 implementation listed in the dispatch.

I tried to break fill timing, overlap, halt, costs, PaperExecutor
composition, config identity, and the shipped tests. Reproduction:
`/tmp/gate5_skeptic_repro.py` plus the snippets below.

## Commands

```
python3 -m pytest tests/test_replay.py tests/test_nodes_capital.py tests/test_configs.py -q --tb=short
# 5 failed, 115 passed in 1.25s
# 5 failures are the documented pre-Gate-5 config baseline
# (cohort / tracking / split-c / P16 PENDING hash). Not Gate 5.

python3 -m pytest tests/test_replay.py tests/test_nodes_capital.py \
  tests/test_configs.py::test_run_development_replay_forces_ineligible_caps_and_pins_fill_policy -q
# 76 passed

ruff check …/replay.py …/nodes_capital.py tests/test_replay.py
# All checks passed

python3 -m dskit.pipeline validate configs/run-development-replay.json --adapter intraday_equities
# OK  hash fa648c6b04b289f8533337312caf29efcc33aee3e78fdf709b03b3fbb39411f4

python3 -m dskit.pipeline plan configs/run-development-replay.json --adapter intraday_equities
# same hash; inputs {} ; order [replay]
```

`test_replay.py` is 16 green. Passing tests were the bug: none of them
place a fill or expiry off the tape, halt the expiry bar, or set
`fill_price_field` to anything but the JSON's `"open"`.

## Findings

### Critical 1 — decisions whose fill bar is off the tape (or whose name has no bars) vanish

`ReplayAdapter.replay` only walks names that appear in `bars`
(`replay.py:310-318`). Decisions for any other symbol are dropped with
`fills=skipped=refused=[]`. Decisions whose
`decision_index + fill_bar_offset` is `>= len(seq)` are appended to
`pending` (`replay.py:341`) and never drained.

Proved:

```
# BBB decision, only AAA bars → empty everything
SILENT_ORPHAN True

# one bar, decision at that bar, offset 1 → fill index 1 never visited
SILENT_OFF_TAPE True
```

`DevelopmentReplay.run` refuses any bar at or after `evidence_end+1d`
UTC (`replay.py:598-609`). A last-included-day decision therefore cannot
be given its next-bar open: include the fill bar and `run` refuses;
omit it and the adapter is silent. This is a wrong fill (missing) with
no skip/refuse row. Phase 5 item 5 required refused candidates to stay
observable. They disappear.

**Fix:** refuse or skip with a named reason (`unknown_symbol`,
`fill_bar_past_tape`); for the evidence bound, either allow a
fill-only trailing bar that cannot carry a new decision, or refuse
last-day decisions explicitly.

### Critical 2 — expiry past the last bar never exits; the lot is discarded

`HorizonBook` stores `expiry_index = fill_index + lead` (`replay.py:245`)
and `expiring()` matches `== index` (`replay.py:257-263`). There is no
end-of-tape flush. After `_replay_symbol` returns, `_lots` is gone.

Proved: two bars, h1 entry at index 1, expiry at index 2 →
`kinds == ["entry"]`, `UNCLOSED_NO_EXIT True`.

Any lot whose expiry is after the last included bar — including every
h1 opened on the last fillable bar, and every h10 opened in the last
ten fill bars — is silently unclosed. Tests always supply extra bars
after expiry (`test_forced_exit_is_fill_bar_plus_lead_at_that_bar_open`
uses four bars for lead 2), so they cannot fail this.

`lead <= 0` is not validated. `lead=0` opens at index 1 with
`expiry_index=1` *after* that tick's `_process_exits`, so the equality
never fires and the lot never exits. `lead=-1` expires at 0, also
never. Same missing-exit class.

**Fix:** flush remaining lots at end-of-tape (or refuse the run);
require `lead` to be an int `>= 1`; use `>=` only if a halt-skip of the
expiry tick is also specified (see Major 1).

### Major 1 — halt does not skip forced exits; `is` fail-opens non-bool halt flags

ADR-0117 proposed: “Halt: skip every action for that symbol on that
tick.” Kickoff said a halted symbol is skipped not queued, and also
“forced exit … using that bar’s open.” The code does exits first, then
skips only pending *entries* (`replay.py:347-358`).

Proved: h1 entry at 2000, expiry bar 3000 with `halted=True` →
`EXIT_ON_HALT True` (exit fill at 12.0). `skipped` is empty. If halt
*did* skip that exit, `expiring(..., 3)` is empty (equality), so the
lot would stick — the ADR rule is internally inconsistent with `==`
expiry. No shipped test halt-pins the expiry bar.

`halted = bar.get(halt_field) is halted_true` (`replay.py:350`). Missing
key or `halted=1` is not halted. Proved: `halted: 1` on the fill bar →
`HALT_1_FAIL_OPEN True` (entry at 2000). JSON `true` is fine; a store
int/float is a silent trade during halt.

Entry skip-not-queue *is* pinned
(`test_a_halted_symbol_is_skipped_not_queued`) and matched the owner
ruling for new fills.

**Fix:** decide halt-vs-exit in the ADR (do not silently pick one);
test it; compare halt with `==`, and refuse a missing halt field.

### Major 2 — PaperExecutor is a locked-quote rubber stamp; no production ledger path

`ReplayAdapter` walks bars itself with `TestClock`, not `ServeLoop` /
`ReplayFeed` / `ReplayClock` (AST: those names are absent; Gate 5a
already proved those seams work and must not be reinvented as a
parallel engine). It builds `Quote(bid=ask=mid=open)` (`replay.py:409`),
submits IOC, then **ignores `ack.fee`** and writes Schwab onto a private
float row (`replay.py:373-376`, `399-402`, `470-480`).

Proved on the live objects:

```
acks [
  ('entry-AAA-1-1', 'filled', Decimal('11.0'), Decimal('0'), 'buy', 11.0),
  ('exit-AAA-1-2',  'filled', Decimal('12.0'), Decimal('0'), 'sell', 12.0),
]
rows [('entry', 11.0, 0.0242…), ('exit', 12.0, 0.0285972…)]
```

`paper_fees=none` so the venue charges nothing. Schwab is applied
once, not twice — but only on the adapter dict. Production folding
`Ack`s would see `fee=0`. Money is `float`; `dskit.production` refuses
floats on money fields. Dummy `digest = "a"*64` (`replay.py:346`).
`run-development-replay.json` notes claim “same decision-graph contract
as production (one graph, one account) with only the fill/clock/feed
boundary replaced.” `plan` reports `inputs: {}` and no account. Phase 5
items 3–5 (crash/restart ledger identity, post-fill solvency, observable
refusals) are absent. That is a false parity claim in the shipped notes,
not an honest “not this commit” gap.

SchwabCostModel *is* the same class EquityKellyMIO now calls
(`nodes_capital.py:543-545`); per-share × qty is correct. That part
holds. The fee never enters PaperExecutor.

**Fix:** either drive fills through ServeLoop+ledger and put Schwab in
the executor fee strategy, or delete the same-graph claim and name
items 3–5 as open. Do not leave a second economic record beside `Ack`.

### Major 3 — fill-model knobs that must drive behavior are validated stickers; tests pin JSON literals

Owner: every fill-model value is a config field, never a Python literal.
Closed vocabularies are listed on FillPolicy and then mostly unused.
Token counts in `replay.py` for
`decision_price_field`, `forced_exit_horizon_basis`, `halt_handling`,
`same_lead_overlap`, `different_lead_overlap`, `same_tick_order`,
`mark_source`, `forced_exit_at`, `cost_model` are the `_PARAMS` /
`_VOCAB` strings only. `HorizonBook` stores `_policy` and never reads
it; expiry is `fill_index + lead` regardless.

`decision_price_field` is dead: fills with `"open"` vs `"close"` are
equal (`DEAD_DECISION_PRICE_FIELD True`). The class *does* read
`fill_price_field` (setting it to `"close"` fills at 11.5) — but
`test_next_bar_open_fill_uses_only_the_config_offset_and_price_field`
asserts `price == 11.0`, which is also `bars[1]["open"]`. Hardcoding
`"open"` would still pass. `fill_bar_offset` *is* independently pinned
by `test_a_temp_fill_policy_copy_changes_the_fill_without_editing_python`.
`test_replay_py_does_not_hardcode_fill_model_values` only bans
`params.get(k, default)`, so it cannot see `fill_index + lead`.
`test_shipped_fill_policy_names_every_fill_model_field_and_hashes`
asserts `policy.X == raw[X]` after `setattr` from that same dict.

**Fix:** dispatch on the named fields (or drop them from the document);
add a test that changes `fill_price_field` (and halt/overlap fields)
and fails if the adapter ignores them.

### Minor 1 — docstring / Examples vs CLAUDE.md

Public `validate_params` / `from_path` / `digest` / `paper_params` lack
Parameters / Returns / Raises. Class Examples instantiate (good) and use
trailing `# value` comments (allowed). `DevelopmentReplay.serving_effect`
returns `"pure"` (accurate for a param/input reader; it is not a serve
entry). Not a correctness issue.

### Minor 2 — evidence_end test comment is the wrong epoch

`test_development_replay_refuses_bars_after_evidence_end` comments
`1_760_688_000_000` as `2025-10-17T00:00:00Z`. Actual exclusive UTC
midnight is `1760659200000`. The test still refuses (08:00Z is after
midnight). The bound itself is a full UTC day; I checked 16:00Z / 20:00Z
/ 23:59:59Z accepted and next midnight refused.

## Not defects (tried)

- Next-bar fill uses `fill_bar_offset` from the document (offset 2 →
  12.0 at 3000).
- h1 does *not* fill-and-exit on the same open; expiry is fill+lead, as
  ADR-0117 *proposes*. Owner-literal `t+h` from the decision bar is not
  what the code does. ADR status remains `proposed`. Do not treat the
  shipped timestamps as an accepted ruling.
- Same-lead refuse, different-lead concurrent, same-tick re-entry after
  expiry: behavior matches the proposal and is pinned.
- Halted *entry* is skipped, not queued to the next open bar.
- `deployment_eligible=true` refuses; digest pin matches; notes stripped
  from fill-policy identity; unknown knobs refuse; closed vocab refuses
  `queue` / `override`.
- No `dskit/production/*.py` edit. No store/tracking on
  `run-development-replay.json`. `_NON_MARKET_RUN_DOCS` keeps it out of
  the MLflow/store cohort tests.
- Spread is not in the quote (by design for fill-at-open); Schwab
  half-spread is the fee. Not double-charged on the adapter row.

## Unrun / unknown

TDD red-first is not evidenced: one commit adds tests and implementation
together. I did not run the full repo suite. I did not load P16 or any
market store. Crash/restart ledger identity has no test to run.

## Reproducibility / handoff

Independent method FAIL. Replay/production parity is not met: the child
owns a private fill loop, private float fees, and no ledger. Tape-boundary
silence is a correctness defect in the shipped adapter, not “later Phase 5
scope.” Overlap/expiry remains ADR-0117-proposed. Do not self-accept it to
close this review.
