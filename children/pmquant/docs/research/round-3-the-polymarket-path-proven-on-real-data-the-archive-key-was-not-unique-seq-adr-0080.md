Verdict: the Polymarket path is proven on real data end to end, and the real data found a defect no synthetic test had — the archive key was not unique.

## What was run for real

- A live Gamma `events` pull for `london-daily-weather` (2026-08-08 to 08-11): 33 settled markets with their `clobTokenIds` and a 0.05 fee rate.
- One real hour of the pmxt L2 archive off the Hugging Face mirror (`hours/2026/08/09/polymarket_orderbook_2026-08-09T12.parquet`, about 360 MB, anonymous, cleaned after filtering): 139,733 rows for those markets — 139,525 `price_change` and 208 `book` events over 44 assets.
- The same pull again after the fixes, this time with NO `token_ids`: the pack resolved 44 token ids from the series slug itself, consulted the mirror's sync state, and the read-back dedup succeeded.

## The defect real data found

The archive key `(asset_id, ts, event_type)` collided 2,837 times with differing rows: a `price_change` row is one price level's update, several levels change within one millisecond, and the same level appears up to three times in one millisecond with different sizes. The read seam rightly refused the pull as ambiguous. Rows now carry `seq`, the within-millisecond ordinal in the file's own row order (numbered BEFORE any filter, so a wider event-type vocabulary inserts rather than renumbers), and the stream keys on `(asset_id, ts, seq)`. A replay applies rows in `(ts, seq)` order.

## Also landed

- ADR-0080: a closed market is dated at the venue's own `closedTime` (live spelling `2026-09-04 12:11:51+00`), `end_date` only as the fallback; a closed market whose instant still lies ahead refuses by name. Early resolutions no longer refuse the whole pull.
- The archive resolves token ids from `series_slugs` (declared `token_ids` win; the resolution inherits `closed`, stated in the knob note), and reads the mirror's `meta/pmxt-polymarket-sync-state.json`: a known gap is skipped with a LOG and the cursor advances; an hour past `latest_available_hour` stops with "not mirrored yet" and the cursor stays.
- The child's shipped Polymarket slugs were guessed spellings that pull nothing; they are now the venue's own (`<city>-daily-weather`, `<city>-daily-lowest-temperature`), pinned by test. `HF_TOKEN` is named in `.env.example`.

## Skeptic review (Opus)

Fixed: `seq` numbered after the event-type filter (would have rewritten stored keys); key ⊆ schema unpinned; a docstring and ADR-0080 describing a refusal narrower than the code's; the token resolution silently inheriting `closed`; a mid-hour gap stamp unpinned. Rejected after checking: batch-boundary numbering, timezone handling, the cursor after a not-mirrored stop, duplicated Gamma spellings, the fee regime key.
