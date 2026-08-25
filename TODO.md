# TODO

- [x] Create a concise `CLAUDE.md` for this project.
- [x] ADR-0020 integrity-parity pass (2026-08-24) closed the deferred
      loud-not-silent register: FileStore OSError wraps + foreign-entry
      doctrine, `\Z` anchors, purity-gate relative-import levels,
      sqlite URI `mode=rw`, storage-key trust on every backend,
      battery coverage for all of it, ruff baseline pinned in
      `pyproject.toml` (classic defaults; tree clean).

Deferred:

- [ ] Engine-level multi-writer coordination (Registry/Lineage
      check-then-act) — needs its own ADR if ever wanted (ADR-0018
      amendment scopes concurrency to the store seam). No consumer
      needs it; leave until one does.
- [ ] Minor hardening declared out of ADR-0020's pass (review-round-3
      residuals, both loud downstream): a move-planted vid appears in
      the wrong kind's id LISTING (every dereference refuses; fixing
      needs O(n) content loads, defeating the sqlite index); and
      `append_event` through a broken events.jsonl symlink creates
      the target where reads refuse it.
