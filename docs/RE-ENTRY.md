# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 1696 pass, 82 skip

**Landed:** tier-2 store packs seam + sqlite pack (ADR-0018) —
`open_store`/`create_store`/`copy_store` in `dskit/assets/store.py`;
backend declared in `store.json` (absent = `file`, old roots
untouched; built-in name or `pkg.module:Class`); `libs/sqlite.py`
(WAL, `synchronous=FULL`, per-call connections, every failure an
`AssetError`). Both CLIs take `init --backend`; `OnboardingRoot`
opens through the seam. Store battery parametrized over backends;
purity gate covers `libs/`. Hardened through six adversarial review
rounds (skeptic loop + workflows): concurrency claims scoped to the
store seam (ADR-0018 amendment — Registry/Lineage mutation stays
one-writer-per-root), snapshot `iter_events` pinned, create refuses
any store artifact leftover, isinstance backend guard, OSError/
sqlite3 wraps, `lexists` pre-check in onboarding layout.

**Next:** open seams — parquet/postgres packs against the settled
seam (sqlite is the template), semantic validation above the engines,
more connector packs. Five deferred loud-not-silent items in
`TODO.md` (FileStore runtime OSError parity, `\Z` regex anchors,
purity-gate relative-import level, `_connect` stray-db, engine
multi-writer ADR). `ruff` still unavailable in the anaconda env —
`pip install -e ".[dev]"` to lint (install unapproved).

**Decisions awaiting user:** none.
