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

Proposed by the pmquant child design (2026-08-26) — thirteen generic gaps,
each with a named dskit home and interim child-side handling. The full
statements are §13 of `docs/children_design_proposals/pmquant.md`; none is an
ADR yet, and none blocks the child from starting:

- [x] 1. `stat_test` evidence self-description (`kinds_stats.py`) — the one
      unported pmquant→dskit engine capability. **Landed via ADR-0033 (2026-08-26).**
- [x] 2. Studentized recentered cluster bootstrap-t as a `stat_test` method
      (`stats.py`) — **unblocked the single-document deploy→size path**.
      **Landed via ADR-0033 (2026-08-26).**
- [x] 3. Registrable family corrections / weighted BH (`stats.py`).
      **Landed via ADR-0033 (2026-08-26).**
- [x] 4. A calibration split block (`cal_start_ms` in `base.TimeSplitConfig`).
      **Landed via ADR-0034 (2026-08-26).**
- [ ] 5. Calibration/scoring `libs/` packs (beta, CORP isotonic, cross-fit,
      Efron lfdr, Venn–Abers, proper scoring rules).
- [ ] 6. Acquire-side coverage hook + guarded parallel acquisition
      (`onboarding/acquire.py`).
- [ ] 7. A grouped/cardinality suite rule (`onboarding/validate.py` `_RULES`).
- [x] 8. Compressed snapshot payloads in onboarding (~96× on gz-class
      archives, ~10× on parquet-class). **Landed via ADR-0036 (2026-08-26;
      the ratified Tier-B sunset path — pmquant's Tier-B bypass retires
      onto it when the child builds).**
- [ ] 9. A generic `records-write` kind beside `table-write`
      (`kinds_table.py`).
- [ ] 10. A generic onboarding-observations reader kind — the second child
      to need it.
- [ ] 11. A records → keyed-table verb (`groupby`/`pivot`, `kinds_flow.py`).
- [ ] 12. Search-seam expressiveness for seed-ensemble studies + per-fold
      node-param binding (`kinds_search.py`, `document.py`/`driver.py`).
- [x] 13. Val-metric checkpoint selection in the torch pack (a `monitor` +
      best-state-restore seam; the curve already computes the row).
      **Landed via ADR-0035 (2026-08-26).**

Found by the first real-data run of `children/intraday_poc` (2026-08-26) —
a child-side defect, not a dskit gap:

- [ ] `intraday_poc` bars-node memory. `IntradayBars._scan()` holds the
      stream about four times over — a 2M-entry `best` dict, a second
      2M-dict `records` list cached permanently as `_snap`, and a third
      full copy in `run()` via `[dict(row) for row in self._scan()]` —
      while `fingerprint()` `json.dumps`es every record into one string
      just to hash it. Measured **14.3 GB peak for a single run** on
      2,013,682 bars against an 18 GB cap; three walk-forward folds OOM
      (observed kill at 17.4 GB). Blocks `run-backtest.json`; the
      onboarding chain and `run-train.json` are green. Config cannot
      reach it — the node takes only `root`/`source`/`stream`. Fix: drop
      the redundant copies and hash incrementally, with a test pinning
      peak. Generalizes — pmquant's ladder data is far larger than 2M
      rows.

Deferred:

- [ ] Engine-level multi-writer coordination (Registry/Lineage
      check-then-act) — needs its own ADR if ever wanted (ADR-0018
      amendment scopes concurrency to the store seam). No consumer
      needs it; leave until one does.
- [ ] Move-planted vid appears in the wrong kind's id LISTING (declared
      out of ADR-0020, round-3 residual, loud downstream: every
      dereference refuses; fixing needs O(n) content loads, defeating
      the sqlite index). Stays declared, not fixed.
