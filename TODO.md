# TODO

- [x] Create a concise `CLAUDE.md` for this project.

Deferred from the ADR-0018 review rounds (pre-existing, loud-not-silent):

- [ ] FileStore runtime ops leak raw `OSError` on damaged/read-only
      roots (`append_event`, `_find`/`list_records`); sqlite wraps its
      equivalents — close the parity gap or declare it.
- [ ] `_SEGMENT`/`_VERSION_ID` are `$`-anchored so one trailing newline
      passes; switch to `\Z`/`fullmatch` (unreachable via `Registry`).
- [ ] Assets purity gate maps every relative import to `dskit.assets`
      regardless of level — a `from ...pipeline import x` in `libs/`
      would slip the static scan.
- [ ] `SqliteStore._connect` on a damaged root recreates a stray empty
      db file before failing; URI `mode=rw` connect would avoid it.
- [ ] Engine-level multi-writer coordination (Registry/Lineage
      check-then-act) — needs its own ADR if ever wanted (ADR-0018
      amendment scopes concurrency to the store seam).
