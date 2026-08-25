# TODO

- [x] Create a concise `CLAUDE.md` for this project.
- [x] ADR-0020 integrity-parity pass (2026-08-24) closed the deferred
      loud-not-silent register: FileStore OSError wraps + foreign-entry
      doctrine, `\Z` anchors, purity-gate relative-import levels,
      sqlite URI `mode=rw`, storage-key trust on every backend,
      battery coverage for all of it, ruff baseline pinned in
      `pyproject.toml` (classic defaults; tree clean).

- [x] `append_event` broken-symlink guard (2026-08-25): FileStore
      mirrors the iter_events squat guard, so a dangling events.jsonl
      symlink refuses loudly instead of creating the target.

- [x] Driver-side stderr streaming of TrainingCurve lines (2026-08-25):
      the parent's StreamHandler hunk is ported — during a run, INFO
      lines stream bare to stderr unless the caller already has a live
      stream handler, and the handler is removed on every exit path.
      Closes the ADR-0025 residual.

Deferred:

- [ ] Engine-level multi-writer coordination (Registry/Lineage
      check-then-act) — needs its own ADR if ever wanted (ADR-0018
      amendment scopes concurrency to the store seam). No consumer
      needs it; leave until one does.
- [ ] Move-planted vid appears in the wrong kind's id LISTING (declared
      out of ADR-0020, round-3 residual, loud downstream: every
      dereference refuses; fixing needs O(n) content loads, defeating
      the sqlite index). Stays declared, not fixed.
