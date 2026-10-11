# RE-ENTRY archive — dskit-core

Previous re-entry notes for this lane, newest first. `/wrap` moves the note it
replaces to the top of this file; nothing is ever edited or removed here. Not
read at session start. Older history: `2026-10-10-consolidated-history.md`.

---

# Active handoff — 2026-10-06: behavior-identical speedups (merged)

**Merged to main as `2ef373d9` (PR #18, fast-forward from `f9f2f905`).** Pure speedups across
index_options (quote/debit backtests: sorted-quotable-strike bisect, per-entry `_expected`
memo, float fast paths, sliced American charges; cdf_study: groupby-indices instead of
per-row filters, linear settled-event history, positional term slopes, files hashed once;
grid: no redundant deepcopies) and dskit (`number_ok` fast path, precompiled ref regexes,
one document hash per run, twCRPS counter walk, batched LightGBM scale/curve predictions,
numpy-pack column reads, `parse_utc` cache, restamp splice, O(1) duplicate-fill check,
metadata read once). Every rewrite keeps the HEAD path as a fallback for subclasses,
non-float/non-ns inputs and errors.

**Evidence.** Full suite (`tests` + index_options) on base and candidate: every baseline
test has the same outcome (10 pre-existing failures, environment-only); 30 new pinning
tests compare fast paths to the original code. Ruff counts unchanged. Measured: PayoffSelect
walk 4.8x, LightGBM rung 20k rows 53s -> 0.6s, decision-weighted curves 32x, candidate
lookup per row 78ms -> 0.07ms, term slopes ~3x, `_read_inventory` 1.6x.

**Review.** Three independent skeptic passes (correctness, tests/integration, closing
correctness): all Critical/Major fixed (subclass `predict_scale` bypass; float32 inputs;
out-of-range macro dates). Open Minors: index_options fast paths restate `leg_intrinsic`
and `number_ok` for floats (marked "same rule as"); decision-region panel lookups and
`_verified_partition` digest reuse have no randomized old-vs-new pin; batched LightGBM
raises ValueError (not LightGBMError) on ragged rows, unreachable.

**Next.** Remote branches `perf/speedups-2026-10-05`, `claude/zealous-goodall-kdllva` and
`run-steps` are fully in main but still exist: this session's proxy refused the deletes.
Four other branches hold unmerged commits and were kept. `torch.py` untouched (byte-pinned).

