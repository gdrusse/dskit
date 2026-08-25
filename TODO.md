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
- [ ] Every backend's `get_record(X)` trusts the storage key: a valid
      foreign record placed under X's filename/row returns a record
      whose `version_id() != X` (rehash checks content-vs-stored-hash,
      not content-vs-requested-key). Shared identically by file/sqlite/
      parquet — add the key check across all three at once. Adjacent:
      parquet point lookups (has/get) silently miss a directory
      squatting `<vid>.parquet` that list/iter refuse loudly.
- [ ] Battery-wide gaps found in the ADR-0019 review: verify-on-
      duplicate-put is untested on file/sqlite (parquet covers its
      own), and FileStore `list_records` returns foreign `*.json`
      stems where parquet now refuses loudly — align or declare.
- [ ] ruff is now available (0.16.4, anaconda) but its defaults flag
      ~47 pre-existing findings tree-wide (I001/ISC004/SIM115/UP017…);
      pin a `[tool.ruff]` baseline in pyproject.toml to codify the
      repo's actual style, then clean deliberately.
