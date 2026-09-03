## Question

A 20-fold walk costs ~35 minutes while every fold's own node timings sum
to 4-7 s. Where do the missing ~105 s per fold go?

## Finding

**All of it is `BarsFromStore` re-reading and re-hashing the whole store,
inside RESOLVE — before the fold's run dir exists, which is why no node
timing could ever show it.**

Measured with a 3-fold profiled walk of `run-jpm-h20-ridge.json`
(cProfile inflates wall time ~2x; ratios hold):

| phase | per fold |
|---|---|
| `_resolve_run` | **212 s** |
| `_execute_plan` | 59 s |
| RECORD (`_write_node_records` + `_write_carry`) | ~0 s |
| GC (all generations, whole run) | 6.6 s/fold |

Inside `_resolve_run`, 100% is `BarsFromStore.fingerprint()`:
`scan_stream` 139 s (gunzip + JSON parse of 8.9M records, 26.6M
`session_name` calls) and `stream_digest` 44 s (sha256 over the same
8.9M records). `_snap` was an INSTANCE attribute, and the driver builds
a fresh source instance per fold by design (`_pin_sources`), so the
memo could never survive a fold boundary. The `alpaca: 0.000s` node
timing was the misdirection: the pinned instance answers EXECUTE from
the scan RESOLVE already paid for.

GC was a plausible suspect (11.5 GB RSS, 90% of one core) and is not
the cause: 191k collections cost 19.8 s across the whole 3-fold run.

**Fix.** The class now caches the snapshot and its fingerprint, keyed on
the params plus `dir_digest(stream_dir(root, source))` — the store's
CONTENT, not its mtimes, so an acquisition rewritten in place still
invalidates (the conformance suite rewrites in place with mtimes
restored, exactly to defeat mtime caches). Hashing 221 MB of gzip costs
0.11 s against the 139 s it saves. A node that has already scanned
answers from its own pin forever, so the driver's resolve/execute
straddle still describes one snapshot.

`dir_digest` graduated into `dskit/onboarding/base.py`; `stream_dir`
into `observations.py`, so `observations/<source>` is spelled once on
the read side.

Expected: fold 2+ setup goes from ~105 s to ~0.1 s; a 20-fold walk from
~35 min to ~8 min, of which ~5 min is the one unavoidable first scan.

## Sources

- Profile: `_resolve_run` 637 s / 3 folds, `fingerprint` 637 s cum,
  `scan_stream` 418 s, `stream_digest` 133 s.
- `dskit/pipeline/driver.py:1156` (`fp = node.fingerprint()` in
  `_pin_sources`), `:1213` `_resolve_run`.
- `children/intraday_equities/intraday_equities/nodes.py` —
  `BarsFromStore._cache_key`, `_scan`, `fingerprint`.
- `tests/test_nodes.py::test_bars_source_scans_once_per_store_content`.
- Still open, and NOT this: `BarsFromStore` has no date bound, so the
  first scan reads 2016-onward even for a 2022 walk.
